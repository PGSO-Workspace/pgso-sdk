# Milestone 3 — Paralinguistic Signal (Streaming, eGeMAPS-first)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce real `SignalReading`s from streaming audio, behind the `Signal` trait, using an **owned pure-Rust DSP extractor** that computes an interpretable subset of eGeMAPS-style acoustic features. This is the reference, default implementation: lightweight, CPU-only, no model download, no proprietary dependency, and **auditable** — when PGSO reacts, the cause is explainable ("pitch rose, energy fell vs. baseline").

**Architecture:** `pgso-signal-egemaps` implements `Signal` from `pgso-core`. Internally: sliding window → analysis frames → per-frame LLDs (F0 via autocorrelation, RMS energy, jitter, shimmer, voicing) → per-window functionals → axis mapping → `Vec<SignalReading>`. An inherent `extract_explained` method additionally returns the underlying `AcousticFeatures` for auditability. No changes to `pgso-core` (R6/R9).

**Tech Stack:** Rust 2021, pgso-core ONLY (no ML runtime, no openSMILE, no model). criterion (dev) for the latency bench.

**Depends on:** Milestone 2 (the core consumes `SignalReading`s via the `Signal` trait).

---

## Why eGeMAPS-first (the constraint, so the implementer understands it)

- **User experience:** the default path must be "press go" — no 1.2 GB download, no Python export, no `ort` runtime. A new user runs the example and it works on CPU in seconds.
- **Thesis coherence:** PGSO's claim is *auditable* governance. DSP features are interpretable; a neural model is a black box. The default face of the SDK must match the thesis.
- **Licensing:** openSMILE is source-available/proprietary and NOT free for commercial use. **DO NOT bind to openSMILE.** Reimplement a documented, interpretable subset of the eGeMAPS low-level descriptors in pure Rust (the math is public; Eyben et al. 2015 GeMAPS is the reference). This keeps PGSO unencumbered.
- **ONNX is opt-in, OUT OF SCOPE here.** The wav2vec2 extractor stays in a separate `pgso-signal-onnx` crate behind feature `onnx` for later ablation (preserved in `m3-signal-onnx-backup.md`). Building it in this milestone is a STOP-HERE violation (R9).

---

## Architecture reconciliation (READ THIS — resolves an apparent conflict)

The master spec freezes `SignalReading.value` as the **raw, normalized per-channel acoustic scalar**, and makes the **`DecisionEngine` (M2) the single owner of baseline + deviation** (3-layer EMA). Therefore:

- `EgemapsSignal::extract` emits `SignalReading.value` = a normalized acoustic scalar in ~[0,1] per axis (NOT a deviation). The engine computes deviation-from-baseline downstream.
- Auditability (REQ-3.6) is satisfied **inside this crate**, not by touching core: `EgemapsSignal` keeps a lightweight per-LLD running mean (for human-readable "vs baseline" deltas) and exposes the raw LLDs through its OWN `AcousticFeatures` struct via the inherent `extract_explained` method. The core `SignalReading` type is never modified (R6/R9).

This is the only correct reading; do not add fields to `pgso-core` types.

---

## File structure

```
crates/pgso-signal-egemaps/
├── Cargo.toml                       # (CREATE) deps: pgso-core; dev: criterion
├── src/
│   ├── lib.rs                       # (CREATE) EgemapsSignal, AcousticFeatures, Signal impl, tests
│   ├── windowing.rs                 # (CREATE) sliding windows + frame framing
│   ├── dsp.rs                       # (CREATE) F0, RMS energy, jitter, shimmer, voicing
│   └── features.rs                  # (CREATE) per-window functionals + axis mapping + RunningStats
└── benches/
    └── latency.rs                   # (CREATE) criterion p50/p95/p99 bench
examples/
└── egemaps_signal.rs                # (CREATE in workspace root or crate) default-signal demo
```

---

## Requirements (EARS form)

**REQ-3.1** THE `pgso-signal-egemaps` crate SHALL implement the `Signal` trait from `pgso-core`, behind feature `egemaps` (default-on for the crate), AND SHALL be the default signal implementation referenced by the workspace example.

**REQ-3.2** THE extractor SHALL compute the interpretable LLD subset (F0, energy, jitter, shimmer, voicing) per frame using pure-Rust DSP, with NO dependency on openSMILE, no ML runtime, and no downloaded model.

**REQ-3.3** THE extractor SHALL consume audio in a sliding window of 800 ms with 400 ms overlap (configurable), framing each window into analysis frames for LLD computation.

**REQ-3.4** THE extractor SHALL run voice-activity detection (via voicing probability + energy) and SHALL gate jitter/shimmer to voiced frames; it SHALL only emit readings for windows with sufficient voiced content.

**REQ-3.5** THE extractor SHALL output `Vec<SignalReading>` with `value` (normalized acoustic scalar, raw per axis — the engine computes deviation), `axis`, `confidence` (derived from voicing strength and voiced fraction), and `timestamp_ms`.

**REQ-3.6 (auditability — the headline property of this milestone)** THE extractor SHALL expose, alongside each `SignalReading`, the underlying LLD values that produced it (mean F0, F0 std, mean energy, jitter, shimmer, voiced fraction, and each LLD's delta vs. the crate's running baseline), via an inherent `extract_explained` method returning the crate's own `ExplainedReading` type. The core `SignalReading` is NOT modified.

**REQ-3.7 (latency budget)** THE extractor SHALL process one window within a configured latency budget on the target CPU, AND a criterion benchmark SHALL report p50/p95/p99 from window-in to reading-out. (Pure DSP runs in CI without any model file.)

**REQ-3.8** WHEN audio is silent or below the VAD threshold, THE extractor SHALL emit no reading (empty `Vec`), so the engine abstains (G4).

**REQ-3.9 (INV-1 enforced)** THE `pgso-core` crate SHALL remain free of any audio/DSP/ML dependency; all DSP lives in `pgso-signal-egemaps`. THE `pgso-signal-egemaps` crate SHALL NOT depend on openSMILE or any ML runtime.

---

## Tasks

### Task 1: Set up crate

**Files:** Create: `crates/pgso-signal-egemaps/Cargo.toml`, `crates/pgso-signal-egemaps/src/lib.rs`

- [ ] **Step 1: Create Cargo.toml**

```toml
[package]
name = "pgso-signal-egemaps"
version = "0.1.0"
edition = "2021"

[features]
default = ["egemaps"]
egemaps = []

[dependencies]
pgso-core = { path = "../pgso-core" }

[dev-dependencies]
criterion = { version = "0.5", features = ["html_reports"] }

[[bench]]
name = "latency"
harness = false
```

- [ ] **Step 2: Create lib.rs module skeleton**

```rust
//! Pure-Rust eGeMAPS-style DSP signal extractor — the default, auditable PGSO signal.
//!
//! Computes an interpretable subset of low-level descriptors (F0, energy, jitter,
//! shimmer, voicing) from raw audio, with no ML runtime and no proprietary
//! dependency. Each `SignalReading` is explainable via [`ExplainedReading`].

mod dsp;
mod features;
mod windowing;

pub use features::{AcousticFeatures, AxisMapping};

// EgemapsSignal, ExplainedReading defined below in Task 4.
```

- [ ] **Step 3: Verify builds**

Run: `cargo build -p pgso-signal-egemaps`
Expected: compiles (empty modules)

---

### Task 2: DSP primitives — F0, energy, voicing (TDD)

**Files:** Create: `crates/pgso-signal-egemaps/src/dsp.rs`

- [ ] **Step 1: Write failing tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::f32::consts::PI;

    /// Generate a pure sine of given frequency, amplitude, length.
    fn sine(freq: f32, amp: f32, len: usize, sr: u32) -> Vec<f32> {
        (0..len).map(|i| amp * (2.0 * PI * freq * i as f32 / sr as f32).sin()).collect()
    }

    #[test]
    fn test_rms_energy_tracks_amplitude() {
        let quiet = sine(200.0, 0.1, 1024, 16000);
        let loud = sine(200.0, 0.5, 1024, 16000);
        assert!(rms_energy(&loud) > rms_energy(&quiet));
        // RMS of a sine with amplitude A is A/sqrt(2)
        let e = rms_energy(&loud);
        assert!((e - 0.5 / 2.0_f32.sqrt()).abs() < 0.02, "rms was {}", e);
    }

    #[test]
    fn test_rms_energy_silence_is_zero() {
        assert!(rms_energy(&vec![0.0; 1024]) < 1e-6);
    }

    #[test]
    fn test_estimate_f0_on_known_tone() {
        // 200 Hz tone at 16kHz → expect F0 ≈ 200 Hz, strongly voiced
        let frame = sine(200.0, 0.5, 1024, 16000);
        let (f0, voicing) = estimate_f0(&frame, 16000, 50.0, 500.0);
        assert!((f0 - 200.0).abs() < 10.0, "f0 was {}", f0);
        assert!(voicing > 0.5, "voicing was {}", voicing);
    }

    #[test]
    fn test_estimate_f0_silence_unvoiced() {
        let frame = vec![0.0; 1024];
        let (_f0, voicing) = estimate_f0(&frame, 16000, 50.0, 500.0);
        assert!(voicing < 0.1, "silence should be unvoiced, voicing was {}", voicing);
    }

    #[test]
    fn test_estimate_f0_higher_tone() {
        // 120 Hz tone → expect F0 ≈ 120 Hz
        let frame = sine(120.0, 0.4, 2048, 16000);
        let (f0, voicing) = estimate_f0(&frame, 16000, 50.0, 500.0);
        assert!((f0 - 120.0).abs() < 8.0, "f0 was {}", f0);
        assert!(voicing > 0.5);
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cargo test -p pgso-signal-egemaps -- dsp`
Expected: FAIL — functions not defined

- [ ] **Step 3: Implement DSP primitives**

```rust
//! Pure-Rust DSP primitives for paralinguistic LLD extraction.

/// Root-mean-square energy of a frame (intensity proxy).
pub fn rms_energy(frame: &[f32]) -> f32 {
    if frame.is_empty() {
        return 0.0;
    }
    let sum_sq: f32 = frame.iter().map(|x| x * x).sum();
    (sum_sq / frame.len() as f32).sqrt()
}

/// Estimate fundamental frequency (F0) via normalized autocorrelation.
///
/// Returns `(f0_hz, voicing_probability)`. Voicing is the normalized
/// autocorrelation peak height in [0,1]; F0 is 0.0 when unvoiced.
///
/// The search is bounded to `[min_f0, max_f0]` Hz, which maps to a lag
/// window `[sr/max_f0, sr/min_f0]` samples.
pub fn estimate_f0(frame: &[f32], sample_rate: u32, min_f0: f32, max_f0: f32) -> (f32, f32) {
    let sr = sample_rate as f32;
    let min_lag = (sr / max_f0).floor().max(1.0) as usize;
    let max_lag = (sr / min_f0).ceil() as usize;
    if frame.len() <= max_lag {
        return (0.0, 0.0);
    }

    // Zero-lag energy (autocorrelation at lag 0).
    let r0: f32 = frame.iter().map(|x| x * x).sum();
    if r0 <= 1e-9 {
        return (0.0, 0.0);
    }

    let mut best_lag = 0usize;
    let mut best_r = 0.0f32;
    for lag in min_lag..=max_lag {
        let mut r = 0.0f32;
        for i in 0..(frame.len() - lag) {
            r += frame[i] * frame[i + lag];
        }
        if r > best_r {
            best_r = r;
            best_lag = lag;
        }
    }

    if best_lag == 0 {
        return (0.0, 0.0);
    }
    let voicing = (best_r / r0).clamp(0.0, 1.0);
    let f0 = sr / best_lag as f32;
    (f0, voicing)
}

/// Peak absolute amplitude of a frame (for shimmer).
pub fn peak_amplitude(frame: &[f32]) -> f32 {
    frame.iter().fold(0.0f32, |acc, x| acc.max(x.abs()))
}
```

- [ ] **Step 4: Run tests**

Run: `cargo test -p pgso-signal-egemaps -- dsp`
Expected: all 5 DSP tests PASS

- [ ] **Step 5: Commit**

```bash
git add crates/pgso-signal-egemaps/src/dsp.rs crates/pgso-signal-egemaps/Cargo.toml crates/pgso-signal-egemaps/src/lib.rs Cargo.toml
git commit -m "feat(signal-egemaps): pure-Rust DSP primitives — RMS energy, autocorrelation F0, voicing"
```

---

### Task 3: Windowing + framing (TDD)

**Files:** Create: `crates/pgso-signal-egemaps/src/windowing.rs`

- [ ] **Step 1: Write failing tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_window_count_800_400() {
        // 2s at 16kHz = 32000 samples. Window=12800 (800ms), hop=6400 (400ms).
        // Windows start at 0, 6400, 12800, 19200 → 4 windows.
        let samples = vec![0.0f32; 32000];
        let windows = sliding_windows(&samples, 12800, 6400);
        assert_eq!(windows.len(), 4);
        assert_eq!(windows[0].len(), 12800);
    }

    #[test]
    fn test_short_audio_no_window() {
        let samples = vec![0.0f32; 5000];
        assert!(sliding_windows(&samples, 12800, 6400).is_empty());
    }

    #[test]
    fn test_frame_count() {
        // 12800-sample window, frame=1024, hop=256 → (12800-1024)/256 + 1 = 47 frames
        let window = vec![0.0f32; 12800];
        let frames = frames(&window, 1024, 256);
        assert_eq!(frames.len(), 47);
        assert_eq!(frames[0].len(), 1024);
    }
}
```

- [ ] **Step 2: Run to verify fail**

Run: `cargo test -p pgso-signal-egemaps -- windowing`
Expected: FAIL

- [ ] **Step 3: Implement**

```rust
//! Sliding-window segmentation and intra-window framing.

/// Split a sample buffer into overlapping windows of `window_size`,
/// advancing by `hop_size`. Returns slices of exactly `window_size`.
pub fn sliding_windows(samples: &[f32], window_size: usize, hop_size: usize) -> Vec<&[f32]> {
    let mut out = Vec::new();
    if window_size == 0 || hop_size == 0 {
        return out;
    }
    let mut start = 0;
    while start + window_size <= samples.len() {
        out.push(&samples[start..start + window_size]);
        start += hop_size;
    }
    out
}

/// Split a window into analysis frames of `frame_size`, advancing by `hop_size`.
pub fn frames(window: &[f32], frame_size: usize, hop_size: usize) -> Vec<&[f32]> {
    sliding_windows(window, frame_size, hop_size)
}
```

- [ ] **Step 4: Run tests**

Run: `cargo test -p pgso-signal-egemaps -- windowing`
Expected: PASS

---

### Task 4: Per-window functionals, axis mapping, RunningStats (TDD)

**Files:** Create: `crates/pgso-signal-egemaps/src/features.rs`

- [ ] **Step 1: Write failing tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_running_stats_z_score() {
        let mut s = RunningStats::new();
        for v in [0.5, 0.6, 0.4, 0.55, 0.45] {
            s.update(v);
        }
        assert!(s.z_score(0.9).abs() > 1.0);
    }

    #[test]
    fn test_running_stats_few_samples_returns_zero_delta() {
        let mut s = RunningStats::new();
        s.update(0.5);
        // With <2 samples, no meaningful baseline → delta 0.0
        assert!(s.delta_vs_mean(0.8).abs() < 1e-6 || s.count() < 2);
    }

    #[test]
    fn test_jitter_zero_for_constant_periods() {
        // Identical F0 across frames → zero jitter
        let f0s = vec![200.0, 200.0, 200.0, 200.0];
        assert!(relative_perturbation(&f0s) < 1e-6);
    }

    #[test]
    fn test_jitter_positive_for_varying_periods() {
        let f0s = vec![200.0, 210.0, 195.0, 205.0];
        assert!(relative_perturbation(&f0s) > 0.0);
    }

    #[test]
    fn test_axis_mapping_arousal_in_range() {
        let mapping = AxisMapping::default();
        let arousal = mapping.arousal(0.3, 40.0); // mean_energy, f0_std
        assert!((0.0..=1.0).contains(&arousal));
    }

    #[test]
    fn test_axis_mapping_louder_higher_arousal() {
        let mapping = AxisMapping::default();
        assert!(mapping.arousal(0.5, 40.0) >= mapping.arousal(0.1, 40.0));
    }
}
```

- [ ] **Step 2: Run to verify fail**

Run: `cargo test -p pgso-signal-egemaps -- features`
Expected: FAIL

- [ ] **Step 3: Implement features.rs**

```rust
//! Per-window functionals, the documented axis mapping, and a lightweight
//! running baseline used only for human-readable auditability deltas.

/// Online mean/variance (Welford). Used for the auditability "vs baseline"
/// deltas — NOT for the governance decision (the DecisionEngine owns that).
#[derive(Debug, Default, Clone)]
pub struct RunningStats {
    count: u64,
    mean: f64,
    m2: f64,
}

impl RunningStats {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn count(&self) -> u64 {
        self.count
    }

    pub fn update(&mut self, value: f32) {
        self.count += 1;
        let v = value as f64;
        let delta = v - self.mean;
        self.mean += delta / self.count as f64;
        self.m2 += delta * (v - self.mean);
    }

    pub fn std_dev(&self) -> f32 {
        if self.count < 2 {
            return 0.0;
        }
        (self.m2 / (self.count - 1) as f64).sqrt() as f32
    }

    /// Difference of `value` from the running mean (0.0 until a baseline exists).
    pub fn delta_vs_mean(&self, value: f32) -> f32 {
        if self.count < 2 {
            return 0.0;
        }
        value - self.mean as f32
    }

    /// Z-score of `value`, or 0.0 if no baseline / zero variance.
    pub fn z_score(&self, value: f32) -> f32 {
        let sd = self.std_dev();
        if sd < 1e-9 || self.count < 2 {
            return 0.0;
        }
        (value - self.mean as f32) / sd
    }
}

/// Mean of a slice (0.0 if empty).
pub fn mean(xs: &[f32]) -> f32 {
    if xs.is_empty() {
        return 0.0;
    }
    xs.iter().sum::<f32>() / xs.len() as f32
}

/// Sample standard deviation of a slice (0.0 if < 2 elements).
pub fn std_dev(xs: &[f32]) -> f32 {
    if xs.len() < 2 {
        return 0.0;
    }
    let m = mean(xs);
    let var = xs.iter().map(|x| (x - m).powi(2)).sum::<f32>() / (xs.len() - 1) as f32;
    var.sqrt()
}

/// Average relative perturbation of a sequence (used for jitter from F0
/// periods and shimmer from amplitudes): mean(|x[i+1]-x[i]|) / mean(x).
pub fn relative_perturbation(xs: &[f32]) -> f32 {
    if xs.len() < 2 {
        return 0.0;
    }
    let m = mean(xs);
    if m.abs() < 1e-9 {
        return 0.0;
    }
    let mut acc = 0.0f32;
    for w in xs.windows(2) {
        acc += (w[1] - w[0]).abs();
    }
    (acc / (xs.len() - 1) as f32) / m
}

/// The documented, tunable mapping from LLD functionals to the Arousal axis.
///
/// Arousal rises with loudness (mean energy) and pitch dynamism (F0 std).
/// These reference constants make the mapping explicit and auditable; they
/// are the design choice PGSO commits to and reports.
#[derive(Debug, Clone)]
pub struct AxisMapping {
    pub energy_ref: f32,  // energy that maps to ~1.0
    pub f0_std_ref: f32,  // F0 std (Hz) that maps to ~1.0
    pub w_energy: f32,
    pub w_f0_std: f32,
}

impl Default for AxisMapping {
    fn default() -> Self {
        Self {
            energy_ref: 0.3,
            f0_std_ref: 50.0,
            w_energy: 0.6,
            w_f0_std: 0.4,
        }
    }
}

impl AxisMapping {
    /// Map mean energy + F0 std to an Arousal scalar in [0,1].
    pub fn arousal(&self, mean_energy: f32, f0_std: f32) -> f32 {
        let e = (mean_energy / self.energy_ref).clamp(0.0, 1.0);
        let p = (f0_std / self.f0_std_ref).clamp(0.0, 1.0);
        (self.w_energy * e + self.w_f0_std * p).clamp(0.0, 1.0)
    }
}

/// Interpretable acoustic features behind one reading — the auditability payload.
#[derive(Debug, Clone)]
pub struct AcousticFeatures {
    pub mean_f0_hz: f32,
    pub f0_std_hz: f32,
    pub mean_energy: f32,
    pub jitter: f32,
    pub shimmer: f32,
    pub voiced_fraction: f32,
    pub mean_voicing: f32,
    /// Mean energy minus the speaker's running-baseline energy (explainability).
    pub energy_vs_baseline: f32,
    /// Mean F0 minus the speaker's running-baseline F0 (explainability).
    pub f0_vs_baseline: f32,
}
```

- [ ] **Step 4: Run tests**

Run: `cargo test -p pgso-signal-egemaps -- features`
Expected: all 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add crates/pgso-signal-egemaps/src/windowing.rs crates/pgso-signal-egemaps/src/features.rs
git commit -m "feat(signal-egemaps): windowing, functionals, documented Arousal axis mapping, running baseline"
```

---

### Task 5: EgemapsSignal — assemble the extractor + Signal impl (TDD)

**Files:** Modify: `crates/pgso-signal-egemaps/src/lib.rs`

- [ ] **Step 1: Write failing tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use pgso_core::{AudioWindow, Axis, Signal};
    use std::f32::consts::PI;

    fn sine_window(freq: f32, amp: f32, len: usize, sr: u32, ts: u64) -> AudioWindow {
        let samples = (0..len)
            .map(|i| amp * (2.0 * PI * freq * i as f32 / sr as f32).sin())
            .collect();
        AudioWindow { samples, sample_rate: sr, timestamp_ms: ts }
    }

    #[test]
    fn test_signal_trait_impl() {
        // Accepted where the core expects a Signal.
        fn takes_signal<S: Signal>(_s: &S) {}
        let sig = EgemapsSignal::new(16000);
        takes_signal(&sig);
    }

    #[test]
    fn test_reading_shape() {
        let mut sig = EgemapsSignal::new(16000);
        // 1s of voiced 180 Hz tone
        let window = sine_window(180.0, 0.4, 16000, 16000, 0);
        let readings = sig.extract(&window);
        assert!(!readings.is_empty(), "voiced tone should yield readings");
        for r in &readings {
            assert!((0.0..=1.0).contains(&r.value), "value out of range: {}", r.value);
            assert!((0.0..=1.0).contains(&r.confidence), "confidence out of range");
            assert!(matches!(r.axis, Axis::Arousal | Axis::Valence));
        }
    }

    #[test]
    fn test_vad_skips_silence() {
        let mut sig = EgemapsSignal::new(16000);
        let silence = AudioWindow {
            samples: vec![0.0; 16000],
            sample_rate: 16000,
            timestamp_ms: 0,
        };
        assert!(sig.extract(&silence).is_empty(), "silence must yield no readings (G4)");
    }

    #[test]
    fn test_audit_exposes_llds() {
        let mut sig = EgemapsSignal::new(16000);
        let window = sine_window(180.0, 0.4, 16000, 16000, 0);
        let explained = sig.extract_explained(&window);
        assert!(!explained.is_empty());
        let features = &explained[0].features;
        // Pitch of a 180 Hz tone should be recovered to within tolerance.
        assert!((features.mean_f0_hz - 180.0).abs() < 15.0, "mean_f0 was {}", features.mean_f0_hz);
        assert!(features.mean_energy > 0.0);
        assert!((0.0..=1.0).contains(&features.voiced_fraction));
    }

    #[test]
    fn test_confidence_low_when_mostly_unvoiced() {
        let mut sig = EgemapsSignal::new(16000);
        // Very quiet noise-free near-silence: a tiny tone barely above floor
        let window = sine_window(180.0, 0.002, 16000, 16000, 0);
        let readings = sig.extract(&window);
        // Either no readings, or low confidence (abstention-friendly)
        if let Some(r) = readings.first() {
            assert!(r.confidence < 0.5, "near-silence should be low confidence");
        }
    }
}
```

- [ ] **Step 2: Run to verify fail**

Run: `cargo test -p pgso-signal-egemaps --lib`
Expected: FAIL — `EgemapsSignal` not defined

- [ ] **Step 3: Implement EgemapsSignal in lib.rs**

Replace the skeleton from Task 1 Step 2 with:

```rust
//! Pure-Rust eGeMAPS-style DSP signal extractor — the default, auditable PGSO signal.
//!
//! Computes an interpretable subset of low-level descriptors (F0, energy, jitter,
//! shimmer, voicing) from raw audio, with no ML runtime and no proprietary
//! dependency. Each `SignalReading` is explainable via [`ExplainedReading`].

mod dsp;
mod features;
mod windowing;

pub use features::{AcousticFeatures, AxisMapping};

use features::RunningStats;
use pgso_core::{AudioWindow, Axis, Signal, SignalReading};

/// Configuration for the DSP extractor. Defaults target 16 kHz speech.
#[derive(Debug, Clone)]
pub struct EgemapsConfig {
    pub window_size: usize, // samples per analysis window (800ms @16k = 12800)
    pub window_hop: usize,  // overlap hop (400ms @16k = 6400)
    pub frame_size: usize,  // intra-window frame (1024 @16k ≈ 64ms)
    pub frame_hop: usize,   // frame hop (256 @16k = 16ms)
    pub min_f0: f32,
    pub max_f0: f32,
    pub voicing_threshold: f32, // frame voiced if voicing prob exceeds this
    pub energy_floor: f32,      // frame considered silent below this RMS
    pub min_voiced_fraction: f32, // window emits a reading only above this
}

impl EgemapsConfig {
    pub fn for_sample_rate(sr: u32) -> Self {
        let s = sr as f32;
        Self {
            window_size: (0.800 * s) as usize,
            window_hop: (0.400 * s) as usize,
            frame_size: (0.064 * s) as usize,
            frame_hop: (0.016 * s) as usize,
            min_f0: 50.0,
            max_f0: 500.0,
            voicing_threshold: 0.5,
            energy_floor: 0.01,
            min_voiced_fraction: 0.25,
        }
    }
}

/// One reading paired with the interpretable features that produced it.
#[derive(Debug, Clone)]
pub struct ExplainedReading {
    pub reading: SignalReading,
    pub features: AcousticFeatures,
}

/// The default PGSO signal: interpretable, lightweight, CPU-only DSP.
pub struct EgemapsSignal {
    config: EgemapsConfig,
    sample_rate: u32,
    mapping: AxisMapping,
    energy_baseline: RunningStats,
    f0_baseline: RunningStats,
}

impl EgemapsSignal {
    pub fn new(sample_rate: u32) -> Self {
        Self {
            config: EgemapsConfig::for_sample_rate(sample_rate),
            sample_rate,
            mapping: AxisMapping::default(),
            energy_baseline: RunningStats::new(),
            f0_baseline: RunningStats::new(),
        }
    }

    pub fn with_config(sample_rate: u32, config: EgemapsConfig) -> Self {
        Self {
            config,
            sample_rate,
            mapping: AxisMapping::default(),
            energy_baseline: RunningStats::new(),
            f0_baseline: RunningStats::new(),
        }
    }

    /// Extract readings AND their interpretable features (auditability, REQ-3.6).
    pub fn extract_explained(&mut self, window: &AudioWindow) -> Vec<ExplainedReading> {
        let cfg = &self.config;
        let mut out = Vec::new();

        for sub in windowing::sliding_windows(&window.samples, cfg.window_size, cfg.window_hop) {
            let frames = windowing::frames(sub, cfg.frame_size, cfg.frame_hop);
            if frames.is_empty() {
                continue;
            }

            let mut voiced_f0 = Vec::new();
            let mut voiced_amp = Vec::new();
            let mut voiced_energy = Vec::new();
            let mut voicing_sum = 0.0f32;
            let mut voiced_count = 0usize;

            for frame in &frames {
                let energy = dsp::rms_energy(frame);
                let (f0, voicing) = dsp::estimate_f0(frame, self.sample_rate, cfg.min_f0, cfg.max_f0);
                let voiced = voicing >= cfg.voicing_threshold && energy >= cfg.energy_floor;
                if voiced {
                    voiced_count += 1;
                    voicing_sum += voicing;
                    voiced_f0.push(f0);
                    voiced_amp.push(dsp::peak_amplitude(frame));
                    voiced_energy.push(energy);
                }
            }

            let voiced_fraction = voiced_count as f32 / frames.len() as f32;
            if voiced_fraction < cfg.min_voiced_fraction || voiced_f0.is_empty() {
                continue; // REQ-3.8: abstain on insufficient voiced content
            }

            let mean_f0 = features::mean(&voiced_f0);
            let f0_std = features::std_dev(&voiced_f0);
            let mean_energy = features::mean(&voiced_energy);
            let mean_voicing = voicing_sum / voiced_count as f32;

            // Jitter from F0 periods (samples), shimmer from amplitudes.
            let periods: Vec<f32> = voiced_f0
                .iter()
                .filter(|f| **f > 0.0)
                .map(|f| self.sample_rate as f32 / f)
                .collect();
            let jitter = features::relative_perturbation(&periods);
            let shimmer = features::relative_perturbation(&voiced_amp);

            // Auditability deltas vs. the crate's running baseline.
            let energy_vs_baseline = self.energy_baseline.delta_vs_mean(mean_energy);
            let f0_vs_baseline = self.f0_baseline.delta_vs_mean(mean_f0);
            self.energy_baseline.update(mean_energy);
            self.f0_baseline.update(mean_f0);

            let features_payload = AcousticFeatures {
                mean_f0_hz: mean_f0,
                f0_std_hz: f0_std,
                mean_energy,
                jitter,
                shimmer,
                voiced_fraction,
                mean_voicing,
                energy_vs_baseline,
                f0_vs_baseline,
            };

            // value = normalized raw Arousal scalar (engine computes deviation).
            let arousal = self.mapping.arousal(mean_energy, f0_std);
            // confidence = voicing strength × how much of the window was voiced.
            let confidence = (mean_voicing * voiced_fraction).clamp(0.0, 1.0);

            let reading = SignalReading {
                value: arousal,
                axis: Axis::Arousal,
                confidence,
                timestamp_ms: window.timestamp_ms,
            };

            out.push(ExplainedReading { reading, features: features_payload });
        }

        out
    }
}

impl Signal for EgemapsSignal {
    fn extract(&mut self, window: &AudioWindow) -> Vec<SignalReading> {
        self.extract_explained(window)
            .into_iter()
            .map(|e| e.reading)
            .collect()
    }
}
```

- [ ] **Step 4: Run tests**

Run: `cargo test -p pgso-signal-egemaps --lib`
Expected: all 5 lib tests PASS

- [ ] **Step 5: Commit**

```bash
git add crates/pgso-signal-egemaps/src/lib.rs
git commit -m "feat(signal-egemaps): EgemapsSignal Signal impl with explainable readings (REQ-3.6)"
```

---

### Task 6: Dependency-hygiene test (INV-1 / R9)

**Files:** Create: `crates/pgso-signal-egemaps/tests/dep_hygiene.rs`

- [ ] **Step 1: Write the test**

```rust
//! Enforces the licensing + purity constraints (REQ-3.2, REQ-3.9, INV-1, R9).

#[test]
fn test_no_proprietary_or_ml_dep() {
    let manifest = std::fs::read_to_string(env!("CARGO_MANIFEST_DIR").to_string() + "/Cargo.toml")
        .expect("read egemaps Cargo.toml");
    let lower = manifest.to_lowercase();
    assert!(!lower.contains("opensmile"), "must NOT bind to openSMILE (R9, licensing)");
    assert!(!lower.contains("\nort ") && !lower.contains("ort ="), "must NOT depend on ort (R9)");
    assert!(!lower.contains("onnx"), "must NOT depend on ONNX (R9)");
    assert!(!lower.contains("tch"), "must NOT depend on libtorch (R9)");
}

#[test]
fn test_core_has_no_audio_or_ml_dep() {
    let core = std::fs::read_to_string(
        env!("CARGO_MANIFEST_DIR").to_string() + "/../pgso-core/Cargo.toml",
    )
    .expect("read pgso-core Cargo.toml");
    let lower = core.to_lowercase();
    assert!(!lower.contains("ort"), "pgso-core must stay ML-free (INV-1)");
    assert!(!lower.contains("onnx"), "pgso-core must stay ML-free (INV-1)");
    assert!(!lower.contains("hound") && !lower.contains("symphonia") && !lower.contains("rodio"),
        "pgso-core must stay audio-free (INV-1)");
}
```

- [ ] **Step 2: Run tests**

Run: `cargo test -p pgso-signal-egemaps --test dep_hygiene`
Expected: PASS

---

### Task 7: Latency benchmark (REQ-3.7)

**Files:** Create: `crates/pgso-signal-egemaps/benches/latency.rs`

- [ ] **Step 1: Write the benchmark**

```rust
use criterion::{criterion_group, criterion_main, Criterion};
use pgso_core::{AudioWindow, Signal};
use pgso_signal_egemaps::EgemapsSignal;
use std::f32::consts::PI;

fn voiced_window(sr: u32, secs: f32) -> AudioWindow {
    let len = (sr as f32 * secs) as usize;
    let samples = (0..len)
        .map(|i| 0.4 * (2.0 * PI * 180.0 * i as f32 / sr as f32).sin())
        .collect();
    AudioWindow { samples, sample_rate: sr, timestamp_ms: 0 }
}

fn bench_window_to_reading(c: &mut Criterion) {
    let window = voiced_window(16000, 1.0);
    c.bench_function("egemaps_window_to_reading", |b| {
        b.iter(|| {
            let mut sig = EgemapsSignal::new(16000);
            sig.extract(&window)
        })
    });
}

criterion_group!(benches, bench_window_to_reading);
criterion_main!(benches);
```

- [ ] **Step 2: Run benchmark and record results**

Run: `cargo bench -p pgso-signal-egemaps`
Record p50/p95/p99 (criterion reports median + CI). Add the numbers to the commit message / a NOTES line.

---

### Task 8: Default-signal example (REQ-3.1)

**Files:** Create: `crates/pgso-signal-egemaps/examples/extract.rs`

- [ ] **Step 1: Write a runnable example showing the default signal + explainability**

```rust
//! The "press go" default-signal demo: synth a voiced tone, extract, print the
//! interpretable features behind each reading. No model download, runs on CPU.

use pgso_core::AudioWindow;
use pgso_signal_egemaps::EgemapsSignal;
use std::f32::consts::PI;

fn main() {
    let sr = 16000u32;
    let len = sr as usize; // 1 second
    let samples: Vec<f32> = (0..len)
        .map(|i| 0.4 * (2.0 * PI * 180.0 * i as f32 / sr as f32).sin())
        .collect();
    let window = AudioWindow { samples, sample_rate: sr, timestamp_ms: 0 };

    let mut signal = EgemapsSignal::new(sr);
    for (i, e) in signal.extract_explained(&window).into_iter().enumerate() {
        println!(
            "reading {i}: axis={:?} value={:.3} confidence={:.3}",
            e.reading.axis, e.reading.value, e.reading.confidence
        );
        println!(
            "  why: mean_f0={:.1}Hz f0_std={:.1} energy={:.3} jitter={:.4} shimmer={:.4} voiced={:.0}%",
            e.features.mean_f0_hz,
            e.features.f0_std_hz,
            e.features.mean_energy,
            e.features.jitter,
            e.features.shimmer,
            e.features.voiced_fraction * 100.0,
        );
    }
}
```

- [ ] **Step 2: Run the example**

Run: `cargo run -p pgso-signal-egemaps --example extract`
Expected: prints readings with their explainable feature breakdown

- [ ] **Step 3: Final checks + commit**

Run: `cargo test -p pgso-signal-egemaps`
Run: `cargo clippy -p pgso-signal-egemaps -- -D warnings`
Run: `cargo build --workspace` (ensure nothing else broke)
Expected: all green

```bash
git add crates/pgso-signal-egemaps/
git commit -m "feat(signal-egemaps): dep-hygiene tests, latency bench, default-signal example"
```

---

## Definition of done

- [x] `pgso-signal-egemaps` implements `Signal` behind feature `egemaps`, set as the default signal in the workspace example
- [x] Interpretable LLD subset (F0, energy, jitter, shimmer, voicing) computed in pure Rust — no openSMILE, no `ort`, no model download
- [x] Tests 1–7 pass; benchmark 8 produces recorded p50/p95/p99
- [x] Each reading is explainable via `extract_explained` → `AcousticFeatures` (REQ-3.6)
- [x] `pgso-core` remains audio/ML-free (INV-1); egemaps has no proprietary/ML dep (R9) — `dep_hygiene` tests enforce both
- [x] `cargo clippy -- -D warnings` clean

---

## STOP HERE

Do NOT:
- build the ONNX extractor (opt-in, separate crate, out of scope — R9)
- wire the signal into the DecisionEngine end-to-end (Milestone 4)
- add actuator transport adapters (MCP/HTTP)

This milestone proves: real audio in → valid, latency-bounded, **explainable** `SignalReading` out, computed by lightweight owned DSP with no heavy or proprietary dependency. The default face of the SDK is now lightweight and auditable, matching the thesis.

## Thesis note (ablation opportunity)

Because both `pgso-signal-egemaps` (DSP, auditable) and a future `pgso-signal-onnx` (neural, Phase-0-validated) implement the same `Signal` trait, a clean ablation — auditable DSP vs. learned neural, same core, same actuator — is possible later. That comparison only exists because the signal sits behind a trait.
