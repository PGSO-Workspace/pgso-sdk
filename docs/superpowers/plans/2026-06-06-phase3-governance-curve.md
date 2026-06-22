# Phase 3 — Signal-Quality × Governance-Correctness Curve — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a two-layer measurement experiment that (B) drives the *real* `pgso-core` pipeline with controlled-quality signals and reports the governance-correctness-vs-signal-quality degradation curve + non-punitive cost, and (A) benchmarks three real arousal signals against human arousal per corpus.

**Architecture:** Python orchestration (signal construction, stats, plots) + a thin standalone Rust crate `phase3-drive` that path-depends on `pgso-core` and `pgso-actuator-local` and **reuses all governance logic unchanged** — it only replays reading sequences and records outcomes. Layer B (priority, no gated data) is built and verified first; Layer A runs after and reports exactly which candidates loaded.

**Tech Stack:** Rust (pgso-core, pgso-actuator-local, anyhow, csv, serde, serde_json) · Python 3.13 (numpy, matplotlib, `statistics.NormalDist`, optional `opensmile`, optional `torch`/`transformers`/`librosa`).

**Spec:** `docs/superpowers/specs/2026-06-06-phase3-governance-curve-design.md` (read it first).

**Working rules (enforced):** TDD ping-pong on the Rust crate (test first, sign-off, implement). No `.unwrap()`/`.expect()` outside `#[cfg(test)]`. `cargo clippy -- -D warnings` must pass. ≤2 files per change without sign-off. The SDK crates are **never modified**. Hard stop + escalate after 3 consecutive failed check attempts.

**Commit message footer (every commit):**
```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## File Structure

```
experiments/phase3-governance-curve/
  config.py                 # M0 — ALL pre-registration; print_header()
  eval_stats.py             # M0 — copied verbatim from phase2 (validated)
  layerb_build_signals.py   # M1 — copula construction + achieved-ρ verify gate
  drive/                    # M2 — Rust crate phase3-drive (TDD heart)
    Cargo.toml
    src/main.rs
  layerb_operating_point.py # M3 — empirical 30% deviation_threshold calibration
  layerb_analyze.py         # M4 — oracle=L0 correctness/FP/FN, chance, cost curves, G2, figures
  layera_egemaps2.py        # M5 — reuse phase1 predicted.csv (MUStARD)
  layera_egemaps_full.py    # M6 — openSMILE (MUStARD) + EMOVOME features; cross-corpus head
  layera_transformer.py     # M6 — wav2small (MUStARD)
  layera_compare.py         # M7 — Δρ paired bootstrap + candidate×corpus table
  run_all.py                # M8 — orchestrate B-first then A
  out/                      # generated: layerb_signals.csv, layerb_decisions.csv, results/*.png
docs/phase3-governance-curve-report.md   # M8 — report template incl. §6.1 limitations
```

Each task below is self-contained. Run all `python`/`cargo` commands from `experiments/phase3-governance-curve/` unless noted. On Windows use PowerShell; the commands are shell-agnostic except where noted.

---

## M0 — Scaffold & pre-registration

### Task 0.1: Create the experiment directory and copy `eval_stats.py`

**Files:**
- Create dir: `experiments/phase3-governance-curve/`
- Create: `experiments/phase3-governance-curve/eval_stats.py` (copy of `experiments/phase2-arousal-benchmark/eval_stats.py`)

- [ ] **Step 1: Copy eval_stats.py verbatim**

```bash
mkdir -p experiments/phase3-governance-curve/out/results
cp experiments/phase2-arousal-benchmark/eval_stats.py experiments/phase3-governance-curve/eval_stats.py
```

- [ ] **Step 2: Verify the self-check passes**

Run: `python experiments/phase3-governance-curve/eval_stats.py`
Expected: prints `eval_stats self-check OK | tie-rho=0.89443 ...`

- [ ] **Step 3: Commit**

```bash
git add experiments/phase3-governance-curve/eval_stats.py
git commit -m "phase3(m0): scaffold experiment dir + reuse validated eval_stats"
```

---

### Task 0.2: Write `config.py` (pre-registration, printed at top of every run)

**Files:**
- Create: `experiments/phase3-governance-curve/config.py`

- [ ] **Step 1: Write config.py**

```python
"""
PGSO Phase 3 — Signal-quality × governance-correctness curve: pre-registration.

Two layers, kept separate (see the design spec):
  Layer B (headline): drive the REAL pgso-core with controlled-quality signals;
    measure governance correctness vs signal quality as a CURVE.
  Layer A (secondary): benchmark real arousal signals vs human arousal per corpus.

Everything decision-relevant is fixed HERE and printed at the top of every run.
Post-hoc changes are forbidden and defeat the test.
"""
from pathlib import Path

EXPERIMENT = "phase3-governance-curve"
PRIMARY_AXIS = "arousal"

# --- Layer B: PRE-REGISTERED signal-quality levels (target Spearman ρ to truth) -
# L0/L4 are sanity checks; L3 is where the real DSP signal sits (Phase-1 ρ≈0.206).
LEVELS = [
    ("L0", 1.00),  # oracle — governance must be 100% correct (by construction)
    ("L1", 0.50),  # strong
    ("L2", 0.35),  # moderate
    ("L3", 0.20),  # weak — real DSP signal
    ("L4", 0.00),  # noise — must collapse to base-rate chance
]
RHO_TOL = 0.015          # achieved mean ρ must match target within this (interior levels)
N_SEEDS = 20             # noise realizations per interior level
SEEDS = list(range(N_SEEDS))

# --- Layer B: frozen EngineConfig + operating point ---------------------------
# Shipped governance-demo config, UNCHANGED, except deviation_threshold which is
# set by EMPIRICAL calibration (layerb_operating_point.py) to hit the oracle
# intervene base-rate below. This is a frozen operating point, NOT correctness tuning.
ENGINE_CONFIDENCE_THRESHOLD = 0.5
ENGINE_HYSTERESIS_WINDOW = 3
ENGINE_EMA_ALPHA = 0.1
ENGINE_WARMUP_READINGS = 5
WARMUP_COUNT = 5           # calm readings fed before the test segment
TEST_CONFIDENCE = 0.9      # held fixed: this experiment varies signal VALUE only (G4 axis out of scope)
TARGET_ORACLE_INTERVENE_RATE = 0.30   # top-tertile most-deviated; pre-registered
ORACLE_RATE_TOL = 0.01
SPEAKER_MIN_UTTS = 5       # per-speaker baseline needs ≥ this many utts, else corpus mean

# Governance-correctness definition (see spec §3.6): the ORACLE is the engine's
# decision on the L0 CONSTRUCTED signal, computed once and frozen. A decision at
# L1–L4 is CORRECT iff it matches that utterance's oracle decision. L0 = 100% by
# construction; L4 collapses to base-rate chance = p² + (1-p)² (printed).

# --- Layer A: candidates + field-range position (NOT a pass/fail cutoff) -------
# Field ranges are LITERATURE-VERIFIED before citing (ParaLBench + MSP-Podcast SOTA).
PHASE1_BASELINE_RHO = 0.206
PERMUTATIONS = 5000
BOOTSTRAP_RESAMPLES = 2000
SEED = 42
CANDIDATES = [
    {"id": "egemaps2", "label": "eGeMAPS 2-feature (Phase-1)", "kind": "dsp",
     "commercial_ok": True, "corpora": ["mustard"]},
    {"id": "egemaps_full", "label": "eGeMAPS FULL (88, +linear head)", "kind": "dsp",
     "commercial_ok": True, "corpora": ["mustard", "emovome"]},
    {"id": "wav2small", "label": "Wav2Small (distilled learned)", "kind": "learned",
     "commercial_ok": False, "corpora": ["mustard"]},
]
# Indicative SER arousal field ranges (TO VERIFY before citing in the report):
FIELD_RANGE = {"heuristic_dsp": (0.10, 0.30), "full_dsp": (0.30, 0.50), "large_learned": (0.50, 0.75)}

# --- paths -------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
PHASE1_DIR = PROJECT / "experiments" / "phase1-arousal-mustard"
MANIFEST = PHASE1_DIR / "out" / "manifest.csv"          # human_arousal + speaker (Layer B truth)
PHASE1_PREDICTED = PHASE1_DIR / "out" / "predicted.csv"  # real eGeMAPS arousal (Layer A cand 1)
PHASE1_WAV_DIR = PHASE1_DIR / "out" / "wav"
EMOVOME_FEATURES = PROJECT / "data" / "emovome" / "Audios" / "features_eGeMAPSv02.csv"
EMOVOME_LABELS = PROJECT / "data" / "emovome" / "labels.csv"
DRIVE_MANIFEST = HERE / "drive" / "Cargo.toml"
OUT = HERE / "out"
SIGNALS_CSV = OUT / "layerb_signals.csv"
DECISIONS_CSV = OUT / "layerb_decisions.csv"
ENGINE_CONFIG_JSON = OUT / "layerb_engine_config.json"
RESULTS_DIR = OUT / "results"


def chance_correct(p: float, q: float) -> float:
    """Expected agreement under independence between oracle (rate p) and level (rate q)."""
    return p * q + (1.0 - p) * (1.0 - q)


def print_header() -> None:
    line = "=" * 80
    print(line)
    print(f"PGSO {EXPERIMENT}  |  axis: {PRIMARY_AXIS}  |  CURVE not threshold (neuro-symbolic)")
    print(line)
    print("LAYER B — pre-registered signal-quality levels (target Spearman ρ to truth):")
    for name, rho in LEVELS:
        print(f"  {name}: ρ={rho:.2f}")
    print(f"  seeds/level={N_SEEDS}  ρ-tol={RHO_TOL}  oracle intervene-rate target={TARGET_ORACLE_INTERVENE_RATE}")
    print("  Oracle = engine decision on the L0 CONSTRUCTED signal (frozen). L0=100% by construction;")
    print("  L4 must collapse to base-rate chance p²+(1-p)² (printed at analysis).")
    print(f"  Frozen EngineConfig: conf_thr={ENGINE_CONFIDENCE_THRESHOLD} hyst={ENGINE_HYSTERESIS_WINDOW} "
          f"ema={ENGINE_EMA_ALPHA} warmup={ENGINE_WARMUP_READINGS}; deviation_threshold = empirically calibrated.")
    print("LAYER A — per-candidate, per-corpus ρ vs human arousal; NEVER pooled; field-range position,")
    print(f"  no invented pass/fail. Phase-1 eGeMAPS baseline reference ρ={PHASE1_BASELINE_RHO}.")
    print(f"  permutations={PERMUTATIONS} bootstrap={BOOTSTRAP_RESAMPLES} seed={SEED}")
    print("RULES: no pooling, no tuning to shape a curve, no post-hoc level changes; report what ran.")
    print(line)


if __name__ == "__main__":
    print_header()
```

- [ ] **Step 2: Verify the header prints and paths resolve**

Run: `python config.py` (from `experiments/phase3-governance-curve/`)
Expected: the pre-registration header prints with all five levels and both layers.

- [ ] **Step 3: Verify the Layer B truth source exists**

Run: `python -c "import config as C; print(C.MANIFEST.exists(), C.PHASE1_PREDICTED.exists(), C.EMOVOME_FEATURES.exists())"`
Expected: `True True True`. If any is `False`, STOP and report (do not fabricate).

- [ ] **Step 4: Commit**

```bash
git add experiments/phase3-governance-curve/config.py
git commit -m "phase3(m0): pre-registration config — levels, frozen engine, candidates, paths"
```

---

## M1 — Layer B signal construction (controlled ρ + verify gate)

### Task 1.1: Write `layerb_build_signals.py`

Builds the controlled-quality signals via a normal-scores Gaussian copula that holds the marginal fixed and varies only the coupling to truth, calibrates λ per level by bisection to hit the target tie-aware Spearman, **verifies achieved ρ before use**, and writes `out/layerb_signals.csv` + an initial `out/layerb_engine_config.json`.

**Files:**
- Create: `experiments/phase3-governance-curve/layerb_build_signals.py`

- [ ] **Step 1: Write the script**

```python
"""
Layer B — build controlled-quality signals (RoTBench-style).

Take MUStARD++ human arousal (ground truth) and inject calibrated noise so the
signal's Spearman ρ to truth hits each pre-registered level. The marginal
distribution of the signal is held identical across levels (empirical quantile
back-map); only the COUPLING to truth changes. Achieved ρ is verified (asserted +
printed) BEFORE the signals are used.

Output:
  out/layerb_signals.csv      level,target_rho,seed,utt_id,speaker,a_true,signal_value,warmup_base
  out/layerb_engine_config.json  frozen EngineConfig + replay params (deviation_threshold = initial estimate)
"""
import csv
import json
from statistics import NormalDist

import numpy as np

import config as C
import eval_stats as S

_N = NormalDist()


def _ppf(u):
    return np.array([_N.inv_cdf(min(max(float(p), 1e-6), 1 - 1e-6)) for p in u])


def _cdf(z):
    return np.array([_N.cdf(float(v)) for v in z])


def load_truth():
    """Read manifest -> (utt_ids, speakers, a_true in [0,1]). Drops rows lacking arousal."""
    ids, spks, arous = [], [], []
    with open(C.MANIFEST, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            v = r.get("human_arousal", "").strip()
            if v == "":
                continue
            ids.append(r["scene"])
            spks.append(r.get("speaker", "?"))
            arous.append(float(v))
    a = np.array(arous, float)
    a_true = (a - 1.0) / 8.0  # 1..9 Likert -> [0,1]
    return ids, spks, a_true


def speaker_baselines(spks, a_true):
    """base[spk] = mean a_true for spk if >= SPEAKER_MIN_UTTS, else corpus mean."""
    corpus = float(a_true.mean())
    by = {}
    for s, a in zip(spks, a_true):
        by.setdefault(s, []).append(a)
    base = {}
    for s, xs in by.items():
        base[s] = float(np.mean(xs)) if len(xs) >= C.SPEAKER_MIN_UTTS else corpus
    return base, corpus


def make_signal(za, eps, lmbda, a_true):
    """Normal-scores copula -> signal with same marginal as a_true, ρ controlled by λ."""
    zs = lmbda * za + np.sqrt(max(0.0, 1.0 - lmbda * lmbda)) * eps
    u = np.clip(_cdf(zs), 0.0, 1.0)
    return np.quantile(a_true, u)  # empirical-quantile back-map (rank-preserving)


def calibrate_lambda(za, eps_by_seed, target_rho, a_true):
    """Bisect λ in [0,1] so the MEAN achieved tie-aware Spearman over seeds ≈ target_rho."""
    if target_rho >= 0.999:
        return 1.0
    if target_rho <= 0.001:
        return 0.0
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        achieved = np.mean([S.spearman(make_signal(za, eps, mid, a_true), a_true)
                            for eps in eps_by_seed])
        if achieved < target_rho:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def main():
    C.print_header()
    C.OUT.mkdir(parents=True, exist_ok=True)
    ids, spks, a_true = load_truth()
    n = len(ids)
    base, corpus = speaker_baselines(spks, a_true)
    warmup_base = np.array([base[s] for s in spks], float)
    za = _ppf((S.avg_rank(a_true) - 0.5) / n)
    eps_by_seed = [np.random.default_rng(s).standard_normal(n) for s in C.SEEDS]

    print(f"\n[truth] N={n} utterances | a_true mean={a_true.mean():.3f} sd={a_true.std():.3f} "
          f"| corpus base={corpus:.3f} | speakers={len(base)}")

    rows = []
    print("\n[verify] achieved Spearman ρ per level (MUST match target before use):")
    ok = True
    for name, target in C.LEVELS:
        lmbda = calibrate_lambda(za, eps_by_seed, target, a_true)
        seeds = [0] if name == "L0" else C.SEEDS  # L0 is seed-independent (λ=1, no noise)
        achieved = []
        for seed in seeds:
            s_val = make_signal(za, eps_by_seed[seed], lmbda, a_true)
            achieved.append(S.spearman(s_val, a_true))
            for i in range(n):
                rows.append((name, target, seed, ids[i], spks[i],
                             float(a_true[i]), float(s_val[i]), float(warmup_base[i])))
        mean_a, sd_a = float(np.mean(achieved)), float(np.std(achieved))
        tol = 0.001 if name in ("L0",) else (0.03 if name == "L4" else C.RHO_TOL)
        good = abs(mean_a - target) <= tol
        ok = ok and good
        print(f"  {name}: target={target:.2f}  achieved={mean_a:+.3f} ± {sd_a:.3f}  λ={lmbda:.3f}  "
              f"{'OK' if good else 'OFF-TARGET'}")
    if not ok:
        raise SystemExit("[verify] FAIL — achieved ρ off target; halting (no post-hoc fudge).")

    with open(C.SIGNALS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["level", "target_rho", "seed", "utt_id", "speaker", "a_true", "signal_value", "warmup_base"])
        w.writerows(rows)
    print(f"\n[signals] wrote {len(rows)} rows -> {C.SIGNALS_CSV}")

    # Initial engine config; deviation_threshold = naive 70th pct of |a_true-base|,
    # refined empirically by layerb_operating_point.py.
    dev0 = float(np.percentile(np.abs(a_true - warmup_base), 100 * (1 - C.TARGET_ORACLE_INTERVENE_RATE)))
    cfg = {
        "confidence_threshold": C.ENGINE_CONFIDENCE_THRESHOLD,
        "deviation_threshold": dev0,
        "hysteresis_window": C.ENGINE_HYSTERESIS_WINDOW,
        "ema_alpha": C.ENGINE_EMA_ALPHA,
        "warmup_readings": C.ENGINE_WARMUP_READINGS,
        "population_prior": corpus,
        "warmup_count": C.WARMUP_COUNT,
        "test_confidence": C.TEST_CONFIDENCE,
    }
    with open(C.ENGINE_CONFIG_JSON, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    print(f"[engine-config] initial deviation_threshold={dev0:.4f} (to be calibrated) -> {C.ENGINE_CONFIG_JSON}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it; the verify gate must pass for all five levels**

Run: `python layerb_build_signals.py`
Expected: `[verify]` prints L0 achieved ≈ +1.000, L1 ≈ +0.500, L2 ≈ +0.350, L3 ≈ +0.200, L4 ≈ 0.000, each marked `OK`; then `[signals] wrote ... rows`. If any line is `OFF-TARGET`, the run halts — investigate the copula, do NOT loosen the tolerance.

- [ ] **Step 3: Sanity-check the output marginal is level-independent**

Run:
```bash
python -c "import csv,collections,statistics as st; rows=list(csv.DictReader(open('out/layerb_signals.csv'))); g=collections.defaultdict(list); [g[r['level']].append(float(r['signal_value'])) for r in rows if r['seed']=='0']; [print(k, round(st.mean(v),3), round(st.pstdev(v),3)) for k,v in g.items()]"
```
Expected: mean/sd of `signal_value` are ≈ equal across L0–L4 (same marginal; only coupling differs).

- [ ] **Step 4: Commit**

```bash
git add experiments/phase3-governance-curve/layerb_build_signals.py
git commit -m "phase3(m1): Layer B controlled-quality signal builder + achieved-ρ verify gate"
```

---

## M2 — Layer B Rust driver `phase3-drive` (TDD)

> **TDD ping-pong:** present the test, get sign-off, then implement. The crate path-depends on the SDK crates and reuses all governance logic; only sequence replay + I/O live here.

### Task 2.1: Scaffold the crate with the data types and a failing core test

**Files:**
- Create: `experiments/phase3-governance-curve/drive/Cargo.toml`
- Create: `experiments/phase3-governance-curve/drive/src/main.rs`

- [ ] **Step 1: Write Cargo.toml**

```toml
# Standalone experiment package: NOT a member of the root workspace.
# Path-depends on the SDK crates it drives; reuses governance logic unchanged.
[package]
name = "phase3-drive"
version = "0.0.0"
edition = "2021"
publish = false

[dependencies]
pgso-core = { path = "../../../crates/pgso-core" }
pgso-actuator-local = { path = "../../../crates/pgso-actuator-local" }
anyhow = "1"
csv = "1"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

- [ ] **Step 2: Write main.rs with types, helpers, the drive core, and tests (impl stubbed to fail)**

```rust
//! Phase 3 Layer B driver: feed controlled-quality signals through the REAL
//! pgso-core pipeline and record per-utterance governance outcomes.
//!
//! Reuses `pgso-core` (DecisionEngine, RuleEngine) and `pgso-actuator-local`
//! (LocalActuator) UNCHANGED. The only logic here is reading-sequence replay + I/O.
//!
//! Usage: phase3-drive <signals.csv> <decisions.csv> <config.json>

use std::collections::HashSet;

use anyhow::{Context, Result};
use pgso_actuator_local::LocalActuator;
use pgso_core::{
    pgso_rules, Action, Actuator, Axis, Catalog, DecisionEngine, EngineConfig, RuleEngine, RuleSet,
    ScopeDecision, SignalReading, Tool, ToolId,
};
use serde::{Deserialize, Serialize};

const TS_STEP_MS: u64 = 800;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RuleConfig {
    NonPunitive,
    Punitive,
}

impl RuleConfig {
    const fn label(self) -> &'static str {
        match self {
            Self::NonPunitive => "nonpunitive",
            Self::Punitive => "punitive",
        }
    }
}

/// Frozen run configuration (produced by the Python builder/operating-point).
/// Mirrors `EngineConfig` (which does not derive Deserialize) + replay params.
#[derive(Debug, Deserialize)]
struct DriveConfig {
    confidence_threshold: f32,
    deviation_threshold: f32,
    hysteresis_window: u32,
    ema_alpha: f32,
    warmup_readings: u64,
    population_prior: f32,
    warmup_count: u32,
    test_confidence: f32,
}

impl DriveConfig {
    fn engine_config(&self) -> EngineConfig {
        EngineConfig {
            confidence_threshold: self.confidence_threshold,
            deviation_threshold: self.deviation_threshold,
            hysteresis_window: self.hysteresis_window,
            ema_alpha: self.ema_alpha,
            warmup_readings: self.warmup_readings,
            population_prior: self.population_prior,
        }
    }
}

#[derive(Debug, Deserialize)]
struct SignalRow {
    level: String,
    target_rho: f32,
    seed: u32,
    utt_id: String,
    speaker: String,
    a_true: f32,
    signal_value: f32,
    warmup_base: f32,
}

#[derive(Debug, Serialize)]
struct DecisionRow {
    level: String,
    target_rho: f32,
    seed: u32,
    utt_id: String,
    speaker: String,
    a_true: f32,
    signal_value: f32,
    config: String,
    intervened: bool,
    n_stepup: u32,
    n_prune: u32,
    n_directive: u32,
    protected_present: bool,
    g2_ok: bool,
}

#[derive(Debug, Default, PartialEq, Eq)]
struct Outcome {
    intervened: bool,
    n_stepup: u32,
    n_prune: u32,
    n_directive: u32,
    protected_present: bool,
    g2_ok: bool,
}

fn protected_set() -> HashSet<ToolId> {
    HashSet::from([ToolId::from("escalate")])
}

fn base_catalog() -> Catalog {
    Catalog::new(vec![
        Tool::new("search", "Web Search"),
        Tool::new("close_sale", "Close Sale"),
        Tool::new("escalate", "Escalate to Human"),
    ])
}

/// Both configs share an IDENTICAL predicate (axis Arousal, dev 0, conf 0) so the
/// engine's deviation_threshold is the sole gate and triggering is config-independent.
/// The punitive set targets the protected `escalate` to demonstrate the G2 downgrade.
fn rules_for(cfg: RuleConfig) -> RuleSet {
    match cfg {
        RuleConfig::NonPunitive => pgso_rules! {
            rule "arousal_stepup" {
                axis: Arousal, deviation: 0.0, confidence: 0.0,
                action: Action::RequireStepUp(ToolId::from("close_sale")),
                action: Action::InjectDirective("Tension detected — confirm before closing.".into())
            }
        },
        RuleConfig::Punitive => pgso_rules! {
            rule "arousal_prune" {
                axis: Arousal, deviation: 0.0, confidence: 0.0,
                action: Action::Prune(ToolId::from("close_sale")),
                action: Action::Prune(ToolId::from("escalate"))
            }
        },
    }
}

fn tally(decision: &ScopeDecision, out: &mut Outcome) {
    match &decision.action {
        Action::RequireStepUp(_) => {
            out.n_stepup += 1;
            out.intervened = true;
        }
        Action::Prune(id) => {
            out.n_prune += 1;
            out.intervened = true;
            // RuleEngine downgrades Prune(protected) -> RequireStepUp, so this can
            // never fire for `escalate`; if it ever did, flag a G2 violation.
            if id.as_str() == "escalate" {
                out.g2_ok = false;
            }
        }
        Action::InjectDirective(_) => {
            out.n_directive += 1;
            out.intervened = true;
        }
        Action::Allow => {}
        _ => {}
    }
}

fn step(
    engine: &mut DecisionEngine,
    rule_engine: &RuleEngine,
    actuator: &mut LocalActuator,
    out: &mut Outcome,
    value: f32,
    confidence: f32,
    ts: u64,
) -> Result<()> {
    let reading = SignalReading {
        value,
        axis: Axis::Arousal,
        confidence,
        timestamp_ms: ts,
    };
    if let Some(output) = engine.process(&reading) {
        for decision in rule_engine.evaluate(&output) {
            tally(&decision, out);
            actuator.apply(&decision).context("actuator apply failed")?;
        }
    }
    Ok(())
}

/// Drive ONE utterance through the real engine+rules+actuator for one rule config.
/// K calm warm-up readings at `warmup_base`, then `hysteresis_window` test readings
/// at `signal_value`. Pure given inputs (core reads no clock/RNG) -> deterministic.
fn drive_utterance(dc: &DriveConfig, rule_cfg: RuleConfig, warmup_base: f32, signal_value: f32) -> Result<Outcome> {
    let protected = protected_set();
    let escalate = ToolId::from("escalate");
    let mut engine = DecisionEngine::new(dc.engine_config());
    let rule_engine = RuleEngine::new(rules_for(rule_cfg), protected.clone());
    let mut actuator = LocalActuator::new(base_catalog(), protected);

    let mut out = Outcome {
        g2_ok: true,
        ..Outcome::default()
    };
    let mut ts = 0u64;
    for _ in 0..dc.warmup_count {
        step(&mut engine, &rule_engine, &mut actuator, &mut out, warmup_base, dc.test_confidence, ts)?;
        ts += TS_STEP_MS;
    }
    for _ in 0..dc.hysteresis_window {
        step(&mut engine, &rule_engine, &mut actuator, &mut out, signal_value, dc.test_confidence, ts)?;
        ts += TS_STEP_MS;
    }
    out.protected_present = actuator.current_catalog().contains(&escalate);
    if !out.protected_present {
        out.g2_ok = false;
    }
    Ok(out)
}

fn main() -> Result<()> {
    anyhow::bail!("not implemented yet")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cfg(deviation_threshold: f32, population_prior: f32) -> DriveConfig {
        DriveConfig {
            confidence_threshold: 0.5,
            deviation_threshold,
            hysteresis_window: 3,
            ema_alpha: 0.1,
            warmup_readings: 5,
            population_prior,
            warmup_count: 5,
            test_confidence: 0.9,
        }
    }

    #[test]
    fn far_signal_triggers_nonpunitive() {
        let dc = cfg(0.3, 0.5);
        let o = drive_utterance(&dc, RuleConfig::NonPunitive, 0.2, 0.9).unwrap();
        assert!(o.intervened);
        assert_eq!(o.n_stepup, 1);
        assert_eq!(o.n_directive, 1);
        assert_eq!(o.n_prune, 0);
        assert!(o.protected_present);
        assert!(o.g2_ok);
    }

    #[test]
    fn at_baseline_does_not_trigger() {
        let dc = cfg(0.3, 0.5);
        let o = drive_utterance(&dc, RuleConfig::NonPunitive, 0.5, 0.5).unwrap();
        assert!(!o.intervened);
        assert_eq!(o.n_stepup, 0);
        assert_eq!(o.n_directive, 0);
    }

    #[test]
    fn punitive_never_prunes_protected_g2() {
        let dc = cfg(0.3, 0.5);
        let o = drive_utterance(&dc, RuleConfig::Punitive, 0.2, 0.9).unwrap();
        assert!(o.intervened);
        assert_eq!(o.n_prune, 1); // close_sale only; escalate downgraded
        assert_eq!(o.n_stepup, 1); // escalate Prune -> RequireStepUp (G2 demo)
        assert!(o.protected_present);
        assert!(o.g2_ok);
    }

    #[test]
    fn warmup_far_from_prior_no_spurious_trigger() {
        // base far from prior: the 1st warm-up reading deviates, but the 2nd (at
        // the now-converged baseline) resets the hysteresis run, so warm-up never
        // triggers. Test signal == base -> no real deviation -> no trigger.
        let dc = cfg(0.3, 0.5);
        let o = drive_utterance(&dc, RuleConfig::NonPunitive, 0.95, 0.95).unwrap();
        assert!(!o.intervened);
    }

    #[test]
    fn deterministic() {
        let dc = cfg(0.3, 0.5);
        let a = drive_utterance(&dc, RuleConfig::Punitive, 0.2, 0.85).unwrap();
        let b = drive_utterance(&dc, RuleConfig::Punitive, 0.2, 0.85).unwrap();
        assert_eq!(a, b);
    }
}
```

- [ ] **Step 3: Run the tests — the core fns should PASS, `main` is the only stub**

Run: `cargo test --manifest-path experiments/phase3-governance-curve/drive/Cargo.toml`
Expected: all five `tests::` pass (the drive core is fully implemented). `main` panicking is fine — it isn't exercised by tests.

- [ ] **Step 4: Clippy must be clean**

Run: `cargo clippy --manifest-path experiments/phase3-governance-curve/drive/Cargo.toml -- -D warnings`
Expected: no warnings. (If `unwrap()` in tests is flagged, it is allowed only inside `#[cfg(test)]`; do not add `unwrap` to non-test code.)

- [ ] **Step 5: Commit**

```bash
git add experiments/phase3-governance-curve/drive/
git commit -m "phase3(m2): phase3-drive crate — drive core + G2 tests (TDD), reuses pgso-core unchanged"
```

---

### Task 2.2: Implement `main` (CSV read → drive both configs → CSV write + G2 gate)

**Files:**
- Modify: `experiments/phase3-governance-curve/drive/src/main.rs` (replace the `main` body)

- [ ] **Step 1: Replace the `main` stub with the real implementation**

```rust
fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let signals = args.next().context("usage: phase3-drive <signals.csv> <decisions.csv> <config.json>")?;
    let out_path = args.next().context("usage: phase3-drive <signals.csv> <decisions.csv> <config.json>")?;
    let cfg_path = args.next().context("usage: phase3-drive <signals.csv> <decisions.csv> <config.json>")?;

    let cfg_text = std::fs::read_to_string(&cfg_path).with_context(|| format!("read config: {cfg_path}"))?;
    let dc: DriveConfig = serde_json::from_str(&cfg_text).context("parse config.json")?;

    let mut rdr = csv::Reader::from_path(&signals).with_context(|| format!("open signals: {signals}"))?;
    let mut wtr = csv::Writer::from_path(&out_path).with_context(|| format!("create decisions: {out_path}"))?;

    let mut n = 0usize;
    let mut g2_violations = 0usize;
    for rec in rdr.deserialize() {
        let row: SignalRow = rec.context("parse signal row")?;
        for rule_cfg in [RuleConfig::NonPunitive, RuleConfig::Punitive] {
            let oc = drive_utterance(&dc, rule_cfg, row.warmup_base, row.signal_value)?;
            if !oc.g2_ok {
                g2_violations += 1;
            }
            wtr.serialize(DecisionRow {
                level: row.level.clone(),
                target_rho: row.target_rho,
                seed: row.seed,
                utt_id: row.utt_id.clone(),
                speaker: row.speaker.clone(),
                a_true: row.a_true,
                signal_value: row.signal_value,
                config: rule_cfg.label().to_string(),
                intervened: oc.intervened,
                n_stepup: oc.n_stepup,
                n_prune: oc.n_prune,
                n_directive: oc.n_directive,
                protected_present: oc.protected_present,
                g2_ok: oc.g2_ok,
            })
            .context("write decision row")?;
            n += 1;
        }
    }
    wtr.flush().context("flush decisions.csv")?;
    println!("[drive] wrote {n} decisions | G2 violations: {g2_violations} (MUST be 0)");
    if g2_violations > 0 {
        anyhow::bail!("G2 VIOLATED in {g2_violations} decisions — blocking defect");
    }
    Ok(())
}
```

- [ ] **Step 2: Build, test, clippy**

Run:
```bash
cargo build --release --manifest-path experiments/phase3-governance-curve/drive/Cargo.toml
cargo test --manifest-path experiments/phase3-governance-curve/drive/Cargo.toml
cargo clippy --manifest-path experiments/phase3-governance-curve/drive/Cargo.toml -- -D warnings
```
Expected: builds; all tests pass; no clippy warnings.

- [ ] **Step 3: Smoke-run on the real signals (using the initial config from M1)**

Run (from `experiments/phase3-governance-curve/`):
```bash
cargo run --release --quiet --manifest-path drive/Cargo.toml -- out/layerb_signals.csv out/layerb_decisions.csv out/layerb_engine_config.json
```
Expected: `[drive] wrote <2×rows> decisions | G2 violations: 0 (MUST be 0)`.

- [ ] **Step 4: Commit**

```bash
git add experiments/phase3-governance-curve/drive/src/main.rs
git commit -m "phase3(m2): phase3-drive main — CSV drive of both rule configs + G2 gate"
```

---

## M3 — Empirical operating-point calibration (the 30% gate)

### Task 3.1: Write `layerb_operating_point.py`

Bisects `deviation_threshold` by invoking the **real driver** on the L0 (oracle) signal only, until the non-punitive oracle intervene-rate hits the pre-registered 30% ± 1%. This sets a *mechanism-defined* operating point (EMA drift over the test segment included) and freezes it.

**Files:**
- Create: `experiments/phase3-governance-curve/layerb_operating_point.py`

- [ ] **Step 1: Write the script**

```python
"""
Layer B — calibrate the operating point (deviation_threshold) empirically.

The engine's effective trigger condition over the 3-reading test segment includes
EMA baseline drift, so we set the threshold by RUNNING the real driver on the L0
signal and bisecting until the oracle intervene-rate = the pre-registered target
(30%). The target is fixed; the threshold that achieves it is mechanism-determined,
not tuned to correctness (correctness is not even measurable until this is frozen).
"""
import csv
import json
import subprocess
import tempfile
from pathlib import Path

import config as C


def _write_l0_only(src, dst):
    with open(src, newline="", encoding="utf-8") as f, open(dst, "w", newline="", encoding="utf-8") as g:
        r = csv.reader(f)
        w = csv.writer(g)
        header = next(r)
        w.writerow(header)
        li = header.index("level")
        for row in r:
            if row[li] == "L0":
                w.writerow(row)


def _run_driver(signals_path, decisions_path, cfg):
    with open(C.ENGINE_CONFIG_JSON, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    res = subprocess.run(
        ["cargo", "run", "--release", "--quiet", "--manifest-path", str(C.DRIVE_MANIFEST),
         "--", str(signals_path), str(decisions_path), str(C.ENGINE_CONFIG_JSON)],
        capture_output=True, text=True, check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(f"driver failed: {res.stderr.strip()[-400:]}")


def _nonpunitive_intervene_rate(decisions_path):
    n, k = 0, 0
    with open(decisions_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["config"] != "nonpunitive":
                continue
            n += 1
            if row["intervened"] == "true":
                k += 1
    return (k / n) if n else float("nan")


def main():
    with open(C.ENGINE_CONFIG_JSON, encoding="utf-8") as f:
        cfg = json.load(f)
    tmp = Path(tempfile.mkdtemp())
    l0 = tmp / "l0_signals.csv"
    dec = tmp / "l0_decisions.csv"
    _write_l0_only(C.SIGNALS_CSV, l0)

    lo, hi = 0.0, 1.0
    rate = float("nan")
    print(f"[calibrate] target oracle intervene-rate = {C.TARGET_ORACLE_INTERVENE_RATE} ± {C.ORACLE_RATE_TOL}")
    for it in range(20):
        mid = 0.5 * (lo + hi)
        cfg["deviation_threshold"] = mid
        _run_driver(l0, dec, cfg)
        rate = _nonpunitive_intervene_rate(dec)
        print(f"  iter {it:>2}: deviation_threshold={mid:.4f} -> intervene-rate={rate:.3f}")
        if abs(rate - C.TARGET_ORACLE_INTERVENE_RATE) <= C.ORACLE_RATE_TOL:
            break
        # higher threshold -> fewer triggers -> lower rate
        if rate > C.TARGET_ORACLE_INTERVENE_RATE:
            lo = mid
        else:
            hi = mid

    cfg["deviation_threshold"] = 0.5 * (lo + hi) if abs(rate - C.TARGET_ORACLE_INTERVENE_RATE) > C.ORACLE_RATE_TOL else cfg["deviation_threshold"]
    with open(C.ENGINE_CONFIG_JSON, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    print(f"[calibrate] FROZEN deviation_threshold={cfg['deviation_threshold']:.4f} "
          f"(oracle intervene-rate≈{rate:.3f}) -> {C.ENGINE_CONFIG_JSON}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run calibration**

Run: `python layerb_operating_point.py`
Expected: bisection converges; final line prints a `FROZEN deviation_threshold=...` with `oracle intervene-rate≈0.30`. If it cannot reach 30% ± 1% within 20 iters, STOP and report (the label distribution may be too discrete — escalate, do not loosen the target).

- [ ] **Step 3: Commit**

```bash
git add experiments/phase3-governance-curve/layerb_operating_point.py experiments/phase3-governance-curve/out/layerb_engine_config.json
git commit -m "phase3(m3): empirical operating-point calibration -> frozen deviation_threshold (30% oracle rate)"
```

---

## M4 — Layer B analysis (the headline curve)

### Task 4.1: Write `layerb_analyze.py`

Re-runs the driver on **all** signals with the frozen config, then computes: oracle (L0) intent, correctness/FP/FN per level (mean±sd over seeds + bootstrap CI over utterances), printed chance baseline, the non-punitive vs punitive cost curves, the G2 confirmation, and two figures.

**Files:**
- Create: `experiments/phase3-governance-curve/layerb_analyze.py`

- [ ] **Step 1: Write the script**

```python
"""
Layer B — analysis: governance-correctness curve, FP/FN, chance baseline,
non-punitive vs punitive cost, and the G2-holds-at-all-levels confirmation.

Oracle intent = the engine's decision on the L0 CONSTRUCTED signal (per utterance,
seed-independent). A decision is CORRECT iff it matches that utterance's oracle.
"""
import csv
import json
import subprocess
from collections import defaultdict

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import config as C
import eval_stats as S


def run_full_drive():
    res = subprocess.run(
        ["cargo", "run", "--release", "--quiet", "--manifest-path", str(C.DRIVE_MANIFEST),
         "--", str(C.SIGNALS_CSV), str(C.DECISIONS_CSV), str(C.ENGINE_CONFIG_JSON)],
        capture_output=True, text=True, check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(f"driver failed: {res.stderr.strip()[-400:]}")
    print(res.stdout.strip())


def load_decisions():
    rows = []
    with open(C.DECISIONS_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            r["intervened"] = (r["intervened"] == "true")
            r["g2_ok"] = (r["g2_ok"] == "true")
            for k in ("n_stepup", "n_prune", "n_directive", "seed"):
                r[k] = int(r[k])
            rows.append(r)
    return rows


def main():
    C.print_header()
    run_full_drive()
    rows = load_decisions()

    # G2 confirmation (must hold for EVERY decision at every level/seed/config).
    g2_total = len(rows)
    g2_fail = sum(1 for r in rows if not r["g2_ok"])
    print(f"\n[G2] protected tool never pruned & always served: {g2_total - g2_fail}/{g2_total} "
          f"decisions OK -> {'HELD' if g2_fail == 0 else 'VIOLATED (blocking)'}")
    if g2_fail:
        raise SystemExit("[G2] VIOLATED — blocking defect.")

    # Oracle (L0) intent per utterance, from the non-punitive config (triggering is
    # config-independent; choose either).
    oracle = {r["utt_id"]: r["intervened"] for r in rows if r["level"] == "L0" and r["config"] == "nonpunitive"}
    utts = sorted(oracle)
    p = float(np.mean([1.0 if oracle[u] else 0.0 for u in utts]))  # oracle intervene base-rate

    # Per (level, config): intervened by (seed, utt).
    by = defaultdict(dict)  # (level,config,seed) -> {utt: intervened}
    cost = defaultdict(lambda: defaultdict(list))  # (level,config) -> metric -> per-seed-per-utt values
    for r in rows:
        if r["level"] == "L0":
            continue
        by[(r["level"], r["config"], r["seed"])][r["utt_id"]] = r["intervened"]

    print("\n" + "=" * 84)
    print("LAYER B — GOVERNANCE-CORRECTNESS CURVE (vs oracle; correctness is config-independent)")
    print("=" * 84)
    print(f"oracle intervene base-rate p = {p:.3f}  (target ≈ {C.TARGET_ORACLE_INTERVENE_RATE})")
    print(f"{'level':>6}{'ρ':>7}{'correct':>20}{'FP':>10}{'FN':>10}{'q':>8}{'chance':>9}")
    print("-" * 84)

    curve = {}  # level -> dict
    for name, target in C.LEVELS:
        if name == "L0":
            curve[name] = dict(rho=target, correct=1.0, sd=0.0, ci=(1.0, 1.0), fp=0.0, fn=0.0, q=p,
                               chance=C.chance_correct(p, p))
            print(f"{name:>6}{target:>7.2f}{'1.000 (by constr.)':>20}{0.0:>10.3f}{0.0:>10.3f}{p:>8.3f}"
                  f"{C.chance_correct(p, p):>9.3f}")
            continue
        per_seed_correct, per_seed_fp, per_seed_fn, per_seed_q = [], [], [], []
        utt_correct = defaultdict(list)  # utt -> [correct over seeds] for bootstrap
        for seed in C.SEEDS:
            d = by[(name, "nonpunitive", seed)]
            c = fp = fn = q = 0
            for u in utts:
                iv = d.get(u, False)
                ora = oracle[u]
                c += int(iv == ora)
                fp += int(iv and not ora)
                fn += int((not iv) and ora)
                q += int(iv)
                utt_correct[u].append(int(iv == ora))
            n = len(utts)
            per_seed_correct.append(c / n)
            per_seed_fp.append(fp / n)
            per_seed_fn.append(fn / n)
            per_seed_q.append(q / n)
        mean_c, sd_c = float(np.mean(per_seed_correct)), float(np.std(per_seed_correct))
        q = float(np.mean(per_seed_q))
        # Bootstrap CI over utterances using each utt's mean correctness over seeds.
        uc = np.array([np.mean(utt_correct[u]) for u in utts], float)
        rng = np.random.default_rng(C.SEED)
        boot = [float(np.mean(uc[rng.integers(0, len(uc), len(uc))])) for _ in range(C.BOOTSTRAP_RESAMPLES)]
        ci = (float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5)))
        curve[name] = dict(rho=target, correct=mean_c, sd=sd_c, ci=ci,
                           fp=float(np.mean(per_seed_fp)), fn=float(np.mean(per_seed_fn)),
                           q=q, chance=C.chance_correct(p, q))
        print(f"{name:>6}{target:>7.2f}{mean_c:>13.3f}±{sd_c:.3f}{curve[name]['fp']:>10.3f}"
              f"{curve[name]['fn']:>10.3f}{q:>8.3f}{curve[name]['chance']:>9.3f}")
    print("-" * 84)
    print("L0 must be 1.000 (sanity); L4 correctness must land near its chance column (sanity).")

    # --- non-punitive vs punitive cost per level ---
    print("\n" + "=" * 84)
    print("NON-PUNITIVE COST vs PUNITIVE COUNTERFACTUAL (per interaction)")
    print("=" * 84)
    print(f"{'level':>6}{'ρ':>7}{'stepups/int':>16}{'FP stepups/int':>18}{'blocks/int (punitive)':>24}")
    print("-" * 84)
    cost_curve = {}
    for name, target in C.LEVELS:
        # stepups/interaction (non-punitive), FP stepups/interaction (oracle=0 utts),
        # blocks/interaction (punitive n_prune).
        np_step, np_fp_step, pu_block = [], [], []
        seeds = [0] if name == "L0" else C.SEEDS
        for seed in seeds:
            su = [r for r in rows if r["level"] == name and r["config"] == "nonpunitive" and r["seed"] == seed]
            pu = [r for r in rows if r["level"] == name and r["config"] == "punitive" and r["seed"] == seed]
            if not su:
                continue
            np_step.append(np.mean([r["n_stepup"] for r in su]))
            np_fp_step.append(np.mean([r["n_stepup"] for r in su if not oracle.get(r["utt_id"], False)]))
            pu_block.append(np.mean([r["n_prune"] for r in pu]))
        cost_curve[name] = dict(rho=target, stepups=float(np.mean(np_step)),
                                fp_stepups=float(np.mean(np_fp_step)), blocks=float(np.mean(pu_block)))
        print(f"{name:>6}{target:>7.2f}{cost_curve[name]['stepups']:>16.3f}"
              f"{cost_curve[name]['fp_stepups']:>18.3f}{cost_curve[name]['blocks']:>24.3f}")
    print("-" * 84)
    print("Non-punitive dividend: a false positive costs ONE extra confirmation (stepup), not a blocked")
    print("capability. The correctness curve is identical for both configs; only the cost per error differs.")

    # --- figures ---
    C.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    names = [n for n, _ in C.LEVELS]
    xs = [curve[n]["rho"] for n in names]
    ys = [curve[n]["correct"] for n in names]
    lo = [curve[n]["ci"][0] for n in names]
    hi = [curve[n]["ci"][1] for n in names]
    ch = [curve[n]["chance"] for n in names]
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.plot(xs, ys, "-o", label="governance-correctness")
    ax.fill_between(xs, lo, hi, alpha=0.2, label="95% CI (utterance bootstrap)")
    ax.plot(xs, ch, "--", color="grey", label="base-rate chance")
    for n in names:
        ax.annotate(n, (curve[n]["rho"], curve[n]["correct"]), textcoords="offset points", xytext=(4, 6))
    ax.set_xlabel("signal quality (Spearman ρ to true arousal)")
    ax.set_ylabel("governance-correctness rate")
    ax.set_title("Layer B — governance correctness vs signal quality")
    ax.set_ylim(0, 1.02)
    ax.grid(True, alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(C.RESULTS_DIR / "layerb_correctness_curve.png", dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 5))
    cx = [cost_curve[n]["rho"] for n in names]
    ax.plot(cx, [cost_curve[n]["fp_stepups"] for n in names], "-o", label="non-punitive FP cost (extra confirms/int)")
    ax.plot(cx, [cost_curve[n]["blocks"] for n in names], "-s", label="punitive cost (blocked capabilities/int)")
    ax.axhline(0.5, ls=":", color="grey", label="reference comfort line (discussion aid, NOT a gate)")
    ax.set_xlabel("signal quality (Spearman ρ to true arousal)")
    ax.set_ylabel("cost per interaction")
    ax.set_title("Layer B — non-punitive cost vs punitive counterfactual")
    ax.grid(True, alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(C.RESULTS_DIR / "layerb_cost_curve.png", dpi=130)
    plt.close(fig)
    print(f"\n[charts] {C.RESULTS_DIR}/layerb_correctness_curve.png, layerb_cost_curve.png")

    # machine-readable summary for the report
    with open(C.OUT / "layerb_summary.json", "w", encoding="utf-8") as f:
        json.dump({"p": p, "curve": curve, "cost": cost_curve}, f, indent=2)
    print(f"[summary] {C.OUT / 'layerb_summary.json'}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the analysis**

Run: `python layerb_analyze.py`
Expected: `[G2] ... HELD`; the correctness table with **L0 = 1.000** and **L4 ≈ its chance column**; the cost table showing non-punitive FP cost ≪ punitive blocks; two PNGs written; `layerb_summary.json` written. **This is the headline result.**

- [ ] **Step 3: Commit**

```bash
git add experiments/phase3-governance-curve/layerb_analyze.py experiments/phase3-governance-curve/out/layerb_summary.json experiments/phase3-governance-curve/out/results/
git commit -m "phase3(m4): Layer B analysis — correctness curve, FP/FN, chance, cost curves, G2 confirmation"
```

---

## M5 — Layer A candidate 1 (eGeMAPS 2-feature, MUStARD)

### Task 5.1: Write `layera_egemaps2.py`

Reuses the Phase-1 `predicted.csv` (the real eGeMAPS arousal) and recomputes ρ + permutation + bootstrap CI + per-speaker breakdown + extraction diagnostics with the shared `eval_stats`.

**Files:**
- Create: `experiments/phase3-governance-curve/layera_egemaps2.py`

- [ ] **Step 1: Write the script**

```python
"""
Layer A — candidate 1: eGeMAPS 2-feature (Phase-1), MUStARD++ (acted).
Reuses experiments/phase1-arousal-mustard/out/predicted.csv (real extractor output).
"""
import csv
import json
from collections import defaultdict

import numpy as np

import config as C
import eval_stats as S


def main():
    pred, human, spk = [], [], []
    n_abstain = 0
    with open(C.PHASE1_PREDICTED, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            v = r.get("pred_arousal_wmean", "").strip()
            if r.get("abstained") == "true" or v == "":
                n_abstain += 1
                continue
            pred.append(float(v))
            human.append(float(r["human_arousal"]))
            spk.append(r.get("speaker", "?"))
    p = np.array(pred, float)
    h = np.array(human, float)
    rho = S.spearman(p, h)
    ci = S.bootstrap_ci(p, h, C.BOOTSTRAP_RESAMPLES, C.SEED)
    pp, _ = S.permutation_p(p, h, C.PERMUTATIONS, C.SEED)
    print(f"[egemaps2 / mustard] N={len(p)} abstain={n_abstain}  "
          f"ρ={rho:+.3f}  95% CI [{ci[0]:+.3f},{ci[1]:+.3f}]  perm p={pp:.4f}  Pearson={S.pearson(p,h):+.3f}")

    by = defaultdict(lambda: [[], []])
    for a, b, s in zip(p, h, spk):
        by[s][0].append(a)
        by[s][1].append(b)
    print("  per-speaker ρ (speaker-independent view):")
    for s in sorted(by):
        xa, xb = by[s]
        if len(xa) >= 5 and np.std(xa) > 1e-9:
            print(f"    {s:<14} N={len(xa):>3} ρ={S.spearman(xa, xb):+.3f}")

    out = {"candidate": "egemaps2", "corpus": "mustard", "n": len(p), "rho": rho,
           "ci": ci, "p": pp, "pearson": S.pearson(p, h), "abstain": n_abstain}
    C.OUT.mkdir(parents=True, exist_ok=True)
    with open(C.OUT / "layera_egemaps2.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"[summary] {C.OUT / 'layera_egemaps2.json'}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run**

Run: `python layera_egemaps2.py`
Expected: `ρ ≈ +0.206` (matches Phase 1), CI roughly `[+0.10,+0.31]`, perm p ≈ 0.0002; per-speaker lines; `layera_egemaps2.json` written.

- [ ] **Step 3: Commit**

```bash
git add experiments/phase3-governance-curve/layera_egemaps2.py experiments/phase3-governance-curve/out/layera_egemaps2.json
git commit -m "phase3(m5): Layer A cand 1 — eGeMAPS 2-feature (MUStARD) ρ + CI + per-speaker"
```

---

## M6 — Layer A candidates 2 & 3 (graceful-degrade)

### Task 6.1: Write `layera_egemaps_full.py` (88-param, cross-corpus, transparent linear head)

**Files:**
- Create: `experiments/phase3-governance-curve/layera_egemaps_full.py`

- [ ] **Step 1: Write the script**

```python
"""
Layer A — candidate 2: eGeMAPS FULL (88-param) with a TRANSPARENT linear head.

Cross-corpus, leakage-free: fit a linear head on one corpus, test on the other,
both directions. Plus a single-feature label-free floor (loudness_sma3_amean).
MUStARD 88 features come from openSMILE over the Phase-1 wavs (graceful skip if
`opensmile` is absent); EMOVOME 88 features are pre-extracted on disk.

Corpora are NEVER pooled: the head is trained on one and evaluated on the other.
"""
import csv
import glob
import json
import os

import numpy as np

import config as C
import eval_stats as S

FLOOR_FEATURE = "loudness_sma3_amean"


def load_emovome():
    """EMOVOME: 88 features + human arousal (mean of numeric SAM ratings)."""
    feats = {}
    with open(C.EMOVOME_FEATURES, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        cols = [c for c in rd.fieldnames if c != "file_id"]
        for r in rd:
            feats[r["file_id"]] = {c: float(r[c]) for c in cols}
    labels = {}
    with open(C.EMOVOME_LABELS, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            sam = [r.get(k, "") for k in ("arousal_E_SAM", "arousal_NE1_SAM", "arousal_NE2_SAM", "arousal_NE3_SAM")]
            vals = [float(x) for x in sam if x not in ("", None)]
            if vals:
                labels[r["file_id"]] = float(np.mean(vals))
    ids = [i for i in feats if i in labels]
    cols = [c for c in next(iter(feats.values()))]
    X = np.array([[feats[i][c] for c in cols] for i in ids], float)
    y = np.array([labels[i] for i in ids], float)
    return cols, X, y


def load_mustard_opensmile():
    """openSMILE eGeMAPSv02 over Phase-1 wavs, joined to human arousal. None if opensmile missing."""
    try:
        import opensmile
    except Exception as e:  # noqa: BLE001
        print(f"[egemaps_full / mustard] opensmile not available ({type(e).__name__}); did not run.")
        return None
    human = {}
    with open(C.MANIFEST, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("human_arousal", "").strip():
                human[r["scene"]] = float(r["human_arousal"])
    smile = opensmile.Smile(feature_set=opensmile.FeatureSet.eGeMAPSv02,
                            feature_level=opensmile.FeatureLevel.Functionals)
    rows, ys, cols = [], [], None
    for wav in sorted(glob.glob(str(C.PHASE1_WAV_DIR / "*.wav"))):
        scene = os.path.basename(wav).rsplit("_u.wav", 1)[0]
        if scene not in human:
            continue
        df = smile.process_file(wav)
        if cols is None:
            cols = list(df.columns)
        rows.append(df.iloc[0].to_numpy(dtype=float))
        ys.append(human[scene])
    if not rows:
        print("[egemaps_full / mustard] no wavs processed; did not run.")
        return None
    return cols, np.array(rows, float), np.array(ys, float)


def standardize(train, test):
    mu, sd = train.mean(0), train.std(0)
    sd[sd < 1e-9] = 1.0
    return (train - mu) / sd, (test - mu) / sd


def linear_fit_predict(Xtr, ytr, Xte):
    """Least-squares linear head with intercept; returns predictions on Xte."""
    A = np.hstack([Xtr, np.ones((len(Xtr), 1))])
    w, *_ = np.linalg.lstsq(A, ytr, rcond=None)
    return np.hstack([Xte, np.ones((len(Xte), 1))]) @ w


def single_feature_rho(cols, X, y, feat):
    if feat not in cols:
        return float("nan")
    return S.spearman(X[:, cols.index(feat)], y)


def main():
    results = {"candidate": "egemaps_full", "head": "cross-corpus linear (leakage-free) + single-feature floor"}
    emo = load_emovome()
    mus = load_mustard_opensmile()
    print(f"[egemaps_full] EMOVOME N={len(emo[2])} | "
          f"MUStARD {'N='+str(len(mus[2])) if mus else 'did not run (opensmile absent)'}")

    # Single-feature floor per corpus (fully label-free, auditable).
    results["floor"] = {}
    ecols, eX, ey = emo
    results["floor"]["emovome"] = single_feature_rho(ecols, eX, ey, FLOOR_FEATURE)
    print(f"  floor [{FLOOR_FEATURE}] EMOVOME ρ={results['floor']['emovome']:+.3f}")
    if mus:
        mcols, mX, my = mus
        results["floor"]["mustard"] = single_feature_rho(mcols, mX, my, FLOOR_FEATURE)
        print(f"  floor [{FLOOR_FEATURE}] MUStARD ρ={results['floor']['mustard']:+.3f}")

    # Cross-corpus linear head (only if both corpora available + shared feature columns).
    results["cross_corpus"] = {}
    if mus:
        mcols, mX, my = mus
        shared = [c for c in ecols if c in mcols]
        ei = [ecols.index(c) for c in shared]
        mi = [mcols.index(c) for c in shared]
        # train EMOVOME -> test MUStARD
        Xtr, Xte = standardize(eX[:, ei], mX[:, mi])
        pred = linear_fit_predict(Xtr, ey, Xte)
        rho1 = S.spearman(pred, my)
        ci1 = S.bootstrap_ci(pred, my, C.BOOTSTRAP_RESAMPLES, C.SEED)
        # train MUStARD -> test EMOVOME
        Xtr2, Xte2 = standardize(mX[:, mi], eX[:, ei])
        pred2 = linear_fit_predict(Xtr2, my, Xte2)
        rho2 = S.spearman(pred2, ey)
        ci2 = S.bootstrap_ci(pred2, ey, C.BOOTSTRAP_RESAMPLES, C.SEED)
        results["cross_corpus"] = {
            "train_emovome_test_mustard": {"rho": rho1, "ci": ci1, "n": len(my)},
            "train_mustard_test_emovome": {"rho": rho2, "ci": ci2, "n": len(ey)},
            "shared_features": len(shared),
        }
        print(f"  cross-corpus EMOVOME→MUStARD ρ={rho1:+.3f} CI[{ci1[0]:+.3f},{ci1[1]:+.3f}]")
        print(f"  cross-corpus MUStARD→EMOVOME ρ={rho2:+.3f} CI[{ci2[0]:+.3f},{ci2[1]:+.3f}]")
    else:
        print("  cross-corpus head: needs MUStARD openSMILE features (opensmile absent) — did not run.")

    C.OUT.mkdir(parents=True, exist_ok=True)
    with open(C.OUT / "layera_egemaps_full.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"[summary] {C.OUT / 'layera_egemaps_full.json'}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run (EMOVOME always; MUStARD only if opensmile present)**

Run: `python layera_egemaps_full.py`
Expected: EMOVOME N prints and a floor + (if `opensmile` installed) MUStARD floor and cross-corpus ρ both directions. If `opensmile` is absent, the MUStARD path prints "did not run" and the EMOVOME floor still reports — this is the graceful-degrade path, recorded honestly.

- [ ] **Step 3: Commit**

```bash
git add experiments/phase3-governance-curve/layera_egemaps_full.py experiments/phase3-governance-curve/out/layera_egemaps_full.json
git commit -m "phase3(m6): Layer A cand 2 — eGeMAPS FULL 88 cross-corpus linear head + single-feature floor"
```

---

### Task 6.2: Write `layera_transformer.py` (wav2small, MUStARD; graceful skip)

**Files:**
- Create: `experiments/phase3-governance-curve/layera_transformer.py`

- [ ] **Step 1: Write the script**

```python
"""
Layer A — candidate 3: distilled transformer (audeering/wav2small), MUStARD++.
Reuses the Phase-2 wav2small adapter VERBATIM. CC BY-NC-SA -> research-only
reference ceiling, NOT shippable. Graceful skip if torch/transformers/model absent.
"""
import csv
import json
import sys

import numpy as np

import config as C
import eval_stats as S

PHASE2_DIR = C.PROJECT / "experiments" / "phase2-arousal-benchmark"


def main():
    sys.path.insert(0, str(PHASE2_DIR))
    try:
        import librosa
        import wav2small_model as m
        model = m.load("cpu")
    except Exception as e:  # noqa: BLE001
        print(f"[wav2small / mustard] not available ({type(e).__name__}: {e}); did not run.")
        json.dump({"candidate": "wav2small", "corpus": "mustard", "ran": False},
                  open(C.OUT / "layera_transformer.json", "w", encoding="utf-8"), indent=2)
        return

    pred, human, n_fail = [], [], 0
    with open(C.MANIFEST, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r.get("human_arousal", "").strip():
                continue
            try:
                sig, _ = librosa.load(r["wav_path"], sr=C.SAMPLE_RATE if hasattr(C, "SAMPLE_RATE") else 16000, mono=True)
                if sig.size < int(0.1 * 16000):
                    n_fail += 1
                    continue
                pred.append(float(m.predict_adv(model, sig)[0]))  # idx 0 = arousal
                human.append(float(r["human_arousal"]))
            except Exception as e:  # noqa: BLE001 - record failure, never impute
                print(f"  [fail] {r['scene']}: {type(e).__name__}", file=sys.stderr)
                n_fail += 1
    p, h = np.array(pred, float), np.array(human, float)
    rho = S.spearman(p, h)
    ci = S.bootstrap_ci(p, h, C.BOOTSTRAP_RESAMPLES, C.SEED)
    pp, _ = S.permutation_p(p, h, C.PERMUTATIONS, C.SEED)
    print(f"[wav2small / mustard] N={len(p)} fail={n_fail}  ρ={rho:+.3f} "
          f"CI[{ci[0]:+.3f},{ci[1]:+.3f}] perm p={pp:.4f}  (CC BY-NC-SA: reference ceiling, NOT shippable)")
    json.dump({"candidate": "wav2small", "corpus": "mustard", "ran": True, "n": len(p),
               "rho": rho, "ci": ci, "p": pp, "fail": n_fail, "commercial_ok": False},
              open(C.OUT / "layera_transformer.json", "w", encoding="utf-8"), indent=2)
    print(f"[summary] {C.OUT / 'layera_transformer.json'}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run (downloads the model on first run if torch present)**

Run: `python layera_transformer.py`
Expected: either `ρ=...` for wav2small on MUStARD, or a clean `did not run` line if torch/transformers/model are unavailable. Both outcomes write `layera_transformer.json` with the `ran` flag.

- [ ] **Step 3: Commit**

```bash
git add experiments/phase3-governance-curve/layera_transformer.py experiments/phase3-governance-curve/out/layera_transformer.json
git commit -m "phase3(m6): Layer A cand 3 — wav2small (MUStARD) with graceful skip + NC caveat"
```

---

## M7 — Layer A comparison (Δρ + candidate×corpus table)

### Task 7.1: Write `layera_compare.py`

**Files:**
- Create: `experiments/phase3-governance-curve/layera_compare.py`

- [ ] **Step 1: Write the script**

```python
"""
Layer A — assemble the candidate×corpus ρ table, the learned-vs-DSP Δρ (paired
bootstrap on MUStARD, where both run), and each candidate's field-range position.
Reports exactly which candidates ran. Corpora never pooled.
"""
import csv
import json

import numpy as np

import config as C
import eval_stats as S

PHASE2_DIR = C.PROJECT / "experiments" / "phase2-arousal-benchmark"


def load_json(name):
    p = C.OUT / name
    return json.load(open(p, encoding="utf-8")) if p.exists() else None


def field_position(rho):
    if not np.isfinite(rho):
        return "n/a"
    for label, (lo, hi) in C.FIELD_RANGE.items():
        if lo <= rho < hi:
            return label
    return "above_large_learned" if rho >= 0.75 else "below_heuristic"


def mustard_aligned_egemaps2():
    """Real eGeMAPS arousal per scene (for the paired Δρ vs the learned candidate)."""
    pred, human = {}, {}
    with open(C.PHASE1_PREDICTED, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            v = r.get("pred_arousal_wmean", "").strip()
            if r.get("abstained") == "true" or v == "":
                continue
            pred[r["scene"]] = float(v)
            human[r["scene"]] = float(r["human_arousal"])
    return pred, human


def main():
    C.print_header()
    eg2 = load_json("layera_egemaps2.json")
    egf = load_json("layera_egemaps_full.json")
    tr = load_json("layera_transformer.json")

    print("\n" + "=" * 92)
    print("LAYER A — candidate × corpus  (Spearman ρ vs human arousal; NEVER pooled)")
    print("=" * 92)
    print(f"{'candidate':<34}{'corpus':<10}{'N':>5}{'ρ':>9}{'95% CI':>20}{'field-pos':>20}")
    print("-" * 92)
    if eg2:
        print(f"{'eGeMAPS 2-feature':<34}{'mustard':<10}{eg2['n']:>5}{eg2['rho']:>+9.3f}"
              f"{f'[{eg2['ci'][0]:+.2f},{eg2['ci'][1]:+.2f}]':>20}{field_position(eg2['rho']):>20}")
    if egf:
        for k in ("train_emovome_test_mustard", "train_mustard_test_emovome"):
            cc = egf.get("cross_corpus", {}).get(k)
            if cc:
                corpus = "mustard" if k.endswith("mustard") else "emovome"
                print(f"{'eGeMAPS FULL (xcorp '+k.split('_')[1][:3]+'→'+corpus[:3]+')':<34}{corpus:<10}"
                      f"{cc['n']:>5}{cc['rho']:>+9.3f}{f'[{cc['ci'][0]:+.2f},{cc['ci'][1]:+.2f}]':>20}"
                      f"{field_position(cc['rho']):>20}")
        for corp, rho in egf.get("floor", {}).items():
            print(f"{'eGeMAPS FULL floor (loudness)':<34}{corp:<10}{'-':>5}{rho:>+9.3f}{'-':>20}{field_position(rho):>20}")
    if tr and tr.get("ran"):
        print(f"{'wav2small (NC, ref ceiling)':<34}{'mustard':<10}{tr['n']:>5}{tr['rho']:>+9.3f}"
              f"{f'[{tr['ci'][0]:+.2f},{tr['ci'][1]:+.2f}]':>20}{field_position(tr['rho']):>20}")
    elif tr:
        print(f"{'wav2small (NC, ref ceiling)':<34}{'mustard':<10}  (did not run)")
    print("-" * 92)
    print("Field-range positions are LITERATURE-VERIFIED before citing (see report). No invented pass/fail.")

    # --- Δρ learned vs DSP on MUStARD (paired bootstrap; CI excludes 0 => REAL) ---
    print("\n=== Δρ: learned (wav2small) − DSP, MUStARD, paired bootstrap ===")
    if tr and tr.get("ran"):
        import sys
        sys.path.insert(0, str(PHASE2_DIR))
        import librosa
        import wav2small_model as m
        eg_pred, human = mustard_aligned_egemaps2()
        model = m.load("cpu")
        scenes, pa, pb, h = [], [], [], []
        with open(C.MANIFEST, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                s = r["scene"]
                if s not in eg_pred:
                    continue
                try:
                    sig, _ = librosa.load(r["wav_path"], sr=16000, mono=True)
                    if sig.size < 1600:
                        continue
                    pa.append(float(m.predict_adv(model, sig)[0]))
                    pb.append(eg_pred[s])
                    h.append(human[s])
                except Exception:  # noqa: BLE001
                    continue
        pa, pb, h = np.array(pa), np.array(pb), np.array(h)
        d = S.spearman(pa, h) - S.spearman(pb, h)
        lo, hi = S.paired_bootstrap_delta(pa, pb, h, C.BOOTSTRAP_RESAMPLES, C.SEED)
        verdict = "REAL" if (lo > 0 or hi < 0) else "within noise"
        print(f"  wav2small − eGeMAPS2  N={len(h)}  Δρ={d:+.3f}  95% CI [{lo:+.3f},{hi:+.3f}]  -> {verdict}")
        print("  NB: even a REAL positive Δρ is a CC BY-NC-SA reference ceiling, not a shippable signal.")
    else:
        print("  wav2small did not run -> no learned-vs-DSP Δρ this run.")

    print("\n[done] Layer A reports exactly the candidates that ran above.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run**

Run: `python layera_compare.py`
Expected: the candidate×corpus table (only rows that ran), field-range positions, and — if wav2small ran — the paired Δρ vs eGeMAPS with a CI.

- [ ] **Step 3: Commit**

```bash
git add experiments/phase3-governance-curve/layera_compare.py
git commit -m "phase3(m7): Layer A comparison — candidate×corpus table + learned-vs-DSP Δρ"
```

---

## M8 — Orchestration & report

### Task 8.1: Write `run_all.py`

**Files:**
- Create: `experiments/phase3-governance-curve/run_all.py`

- [ ] **Step 1: Write the orchestrator**

```python
"""
Phase 3 orchestrator: Layer B first (priority, no gated data), then Layer A.
Each stage is also runnable standalone. Stages that need optional deps degrade
gracefully and report; nothing here imputes a missing result.
"""
import subprocess
import sys


def run(desc, *cmd):
    print("\n" + "#" * 80 + f"\n# {desc}\n" + "#" * 80)
    r = subprocess.run([sys.executable, *cmd], check=False)
    if r.returncode != 0:
        print(f"[run_all] stage FAILED: {desc} (rc={r.returncode}) — see output above.")
    return r.returncode


def main():
    # Layer B (headline): build -> calibrate -> analyze (analyze runs the full drive).
    if run("Layer B — build controlled-quality signals", "layerb_build_signals.py"):
        raise SystemExit("Layer B build failed; halting.")
    if run("Layer B — calibrate operating point", "layerb_operating_point.py"):
        raise SystemExit("Layer B calibration failed; halting.")
    run("Layer B — analyze (headline curve + cost + G2)", "layerb_analyze.py")

    # Layer A (secondary): each candidate reports whether it ran.
    run("Layer A — eGeMAPS 2-feature (MUStARD)", "layera_egemaps2.py")
    run("Layer A — eGeMAPS FULL (cross-corpus)", "layera_egemaps_full.py")
    run("Layer A — wav2small (MUStARD)", "layera_transformer.py")
    run("Layer A — comparison + Δρ", "layera_compare.py")
    print("\n[run_all] complete. See out/results/*.png and out/*.json; write up docs/phase3-governance-curve-report.md")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Full run end-to-end**

Run: `python run_all.py`
Expected: Layer B stages complete (G2 HELD, L0=1.000, L4≈chance, cost curves), then each Layer A candidate prints its result or a clean "did not run". No stage imputes a missing value.

- [ ] **Step 3: Commit**

```bash
git add experiments/phase3-governance-curve/run_all.py
git commit -m "phase3(m8): orchestrator — Layer B first, then Layer A (graceful)"
```

---

### Task 8.2: Write the report template with the pre-stubbed §6.1 limitations

The report is filled with real numbers AFTER `run_all.py`. This task creates the template (Phase-1 report style) with the limitations subsection from spec §6.1 already written, so it cannot be forgotten.

**Files:**
- Create: `docs/phase3-governance-curve-report.md`

- [ ] **Step 1: Write the report template**

````markdown
# Phase 3 Report — Signal-Quality × Governance-Correctness Curve

**Project:** PGSO — Paralinguistic Governance for State Orchestration
**Phase:** 3 (final benchmark — measurement)
**Date:** <fill on completion>
**Author:** Lucio (PI)
**Status:** <fill: headline curve verdict>

## Abstract
<2–3 sentences: the curve shape, where the mechanism stops governing correctly, the non-punitive dividend, and where the real signal (L3) sits. Fill from out/layerb_summary.json.>

## 1. Framing
Neuro-symbolic: probabilistic perception + deterministic action. We report a CURVE
(RoTBench-style), not a threshold; Layer A (perception, ParaLBench) is separated
from Layer B (action). Real `pgso-core` driven unchanged.

## 2. Layer B — governance correctness vs signal quality (headline)
- Pre-registered levels L0–L4 (target ρ) and the achieved-ρ verify table (paste from build).
- Frozen EngineConfig + the empirically calibrated operating point (oracle intervene-rate p).
- Figure: `out/results/layerb_correctness_curve.png`. Table: correctness/FP/FN per level with CI.
- L0 = 100% (sanity); L4 ≈ base-rate chance (sanity) — paste the chance column.
- G2: HELD across <N> decisions at every level incl. L4.

## 3. Layer B — the non-punitive argument (quantified)
- Figure: `out/results/layerb_cost_curve.png`. Non-punitive FP cost (extra confirmations/interaction)
  vs punitive counterfactual (blocked capabilities/interaction). The correctness curve is identical
  for both configs; only the cost per error differs.
- The ρ-level at which the non-punitive cost stays modest, and where L3 (real signal) lands.

## 4. Layer A — perception benchmark (per-corpus, never pooled)
- Candidate × corpus ρ table with CIs (paste from layera_compare).
- eGeMAPS-FULL cross-corpus (acted MUStARD ↔ spontaneous EMOVOME), both directions.
- Δρ learned (wav2small) vs DSP, MUStARD, paired bootstrap.
- Field-range position per candidate — **cite verified sources** (see §7).

## 5. The one honest paragraph
<At what signal quality PGSO governs acceptably given the non-punitive reaction, and where each
real signal candidate falls relative to that — i.e., is the library ready, and with which signal.
This paragraph MUST be read together with §6.>

## 6. Limitations

> Required (spec §6.1). The §5 paragraph must NOT be read as "L3 works → the real eGeMAPS signal works."

1. **Synthetic noise ≠ real extractor error (central caveat).** Layer B characterizes mechanism
   robustness to *controlled, stochastic* (Gaussian-copula) degradation. Real extractor error is
   **structured** — speaker-correlated and non-Gaussian (cf. Phase 1's GoldenGirls: high-pitch voices
   systematically pegged the F0-variability term). Therefore **L3's tolerance to synthetic ρ = 0.20
   does NOT establish tolerance to the real eGeMAPS signal's ρ ≈ 0.206.** Whether governance degrades
   equivalently under the real signal's structured error is **not established here**.
2. **Noise-free baseline.** The engine is handed a perfect, zero-variance speaker baseline before each
   test segment; live operation estimates the baseline from noisy readings, which could **compound**
   degradation. The clean baseline isolates the signal-value axis; the curve is not a real-world-
   robustness measurement.
3. **Confidence held fixed.** G4 abstention/confidence degradation is a separate axis held at 0.9;
   this experiment varies signal-value quality only.
4. **Range-restricted, acted, single-corpus Layer-B truth.** MUStARD++ acted arousal (sd ≈ 1.14/9);
   the operating point is tuned to that distribution. eGeMAPS-FULL is the only cross-corpus Layer-A candidate.

## 7. Reproducibility & integrity
- Harness: `experiments/phase3-governance-curve/`. Pipeline: `run_all.py`.
- Pre-registration printed at top of every run; achieved ρ verified before use; corpora never pooled;
  abstentions/skips reported never imputed; no post-hoc level/threshold changes; no tuning to shape a
  curve; real `pgso-core` driven unchanged.
- Field-range citations (verify each before asserting): ParaLBench (IEEE TAC 2024); MSP-Podcast SOTA.

*End of report.*
````

- [ ] **Step 2: Verify the limitations subsection is present and matches the spec**

Run: `python -c "t=open('docs/phase3-governance-curve-report.md',encoding='utf-8').read(); assert 'Synthetic noise' in t and 'GoldenGirls' in t and 'Noise-free baseline' in t; print('limitations stub OK')"`
Expected: `limitations stub OK`

- [ ] **Step 3: Commit**

```bash
git add docs/phase3-governance-curve-report.md
git commit -m "phase3(m8): report template with pre-stubbed §6.1 limitations (synthetic≠real, noise-free baseline)"
```

---

## Final verification (run before claiming completion)

- [ ] **All Rust tests + clippy pass:**

```bash
cargo test --manifest-path experiments/phase3-governance-curve/drive/Cargo.toml
cargo clippy --manifest-path experiments/phase3-governance-curve/drive/Cargo.toml -- -D warnings
```
Expected: tests pass; no warnings.

- [ ] **SDK crates untouched (no governance reimplemented):**

```bash
git diff --stat main -- crates/
```
Expected: **empty** — Phase 3 changes nothing under `crates/`.

- [ ] **Layer B sanity holds:** open `out/layerb_summary.json` — `curve.L0.correct == 1.0` and `curve.L4.correct` is within ~0.03 of `curve.L4.chance`.

- [ ] **G2 held:** `layerb_analyze.py` output contains `[G2] ... HELD`.

- [ ] **Report limitations present:** §6.1 synthetic-vs-real caveat is in `docs/phase3-governance-curve-report.md`.

---

## Self-review notes (author)

- **Spec coverage:** Layer B construction (M1), real-core drive (M2), operating point (M3), correctness/FP/FN + chance + cost + G2 (M4) ✔; Layer A 3 candidates + Δρ + field-range (M5–M7) ✔; deliverables + report incl. §6.1 limitations (M8) ✔; integrity (config header + verify gate + never-pool + graceful skip) ✔.
- **Oracle definition (spec §3.6 fix):** analysis defines oracle on the L0 *constructed* signal's driver decision (M4), never raw `a_true` — matches the corrected spec.
- **Type consistency:** `DriveConfig`/`SignalRow`/`DecisionRow` columns are the single contract between `layerb_build_signals.py`, `layerb_engine_config.json`, the Rust driver, and `layerb_analyze.py`; `eval_stats` functions (`spearman`, `bootstrap_ci`, `permutation_p`, `paired_bootstrap_delta`, `avg_rank`) match the reused module.
- **No placeholders:** every code step is complete; `<fill ...>` markers exist only in the *report narrative*, which is intentionally completed after the runs produce numbers.
````
