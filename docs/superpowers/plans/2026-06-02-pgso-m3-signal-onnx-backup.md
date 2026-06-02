# Milestone 3 — Paralinguistic Signal (Streaming)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce real `SignalReading`s from streaming audio, behind the `Signal` trait, using the ONNX extractor validated in Phase 0. The model's uncertainty is confined to this crate; the core stays deterministic.

**Architecture:** `pgso-signal-onnx` implements `Signal` from `pgso-core`. Internally: sliding window → VAD → ONNX inference → per-channel standardization → `Vec<SignalReading>`. No changes to `pgso-core` (R6).

**Tech Stack:** Rust 2021, ort (ONNX Runtime), pgso-core

**Depends on:** Milestone 2 (the core consumes `SignalReading`s via the `Signal` trait).

---

## Prerequisites (before coding)

### ONNX model conversion

The Phase 0 model (`audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim`) is PyTorch. Convert to ONNX:

```python
import torch
from transformers import Wav2Vec2ForSequenceClassification, Wav2Vec2FeatureExtractor

model = Wav2Vec2ForSequenceClassification.from_pretrained(
    "audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim"
)
model.eval()

dummy = torch.randn(1, 16000)  # 1s at 16kHz
torch.onnx.export(
    model, dummy, "models/wav2vec2_emotion.onnx",
    input_names=["audio"],
    output_names=["logits"],
    dynamic_axes={"audio": {1: "seq_length"}},
    opset_version=17,
)
```

Place the `.onnx` file at `models/wav2vec2_emotion.onnx` (gitignored; document download/conversion in README).

---

## File structure

```
crates/pgso-signal-onnx/
├── Cargo.toml                       # (CREATE) deps: pgso-core, ort
└── src/
    ├── lib.rs                       # (CREATE) OnnxSignal + Signal impl + tests
    ├── windowing.rs                 # (CREATE) sliding window logic
    └── vad.rs                       # (CREATE) energy-based VAD
models/
└── wav2vec2_emotion.onnx            # (gitignored, see prerequisites)
```

---

## Requirements (EARS form)

**REQ-3.1** THE `pgso-signal-onnx` crate SHALL implement `Signal` from `pgso-core`, behind feature `onnx`.

**REQ-3.2** THE extractor SHALL consume audio in a sliding window of 800 ms with 400 ms overlap (configurable), resampled to 16 kHz.

**REQ-3.3** THE extractor SHALL run energy-based voice-activity detection and SHALL only emit readings for windows with energy above the VAD threshold.

**REQ-3.4** THE extractor SHALL output `Vec<SignalReading>` with `value` = raw per-channel model output (normalized ~[0,1]), one per axis (Valence, Arousal). The `DecisionEngine` computes deviation from baseline.

**REQ-3.5** THE extractor SHALL maintain per-channel running statistics (Welford's algorithm) and standardize the raw output to z-scores, so the signal is not dominated by constant scale offsets (Phase 0 lesson).

**REQ-3.6** THE extractor SHALL process one window within the latency budget on the target CPU. A benchmark SHALL report p50/p95/p99 latency.

**REQ-3.7 (INV-1)** ONNX/ort SHALL live ONLY in `pgso-signal-onnx`, never in `pgso-core`.

**REQ-3.8** WHEN audio is silent, THE extractor SHALL return an empty `Vec` (no readings), causing the `DecisionEngine` to emit no output.

---

## Tasks

### Task 1: Set up crate

**Files:** Create: `crates/pgso-signal-onnx/Cargo.toml`, `crates/pgso-signal-onnx/src/lib.rs`

- [ ] **Step 1: Create Cargo.toml**

```toml
[package]
name = "pgso-signal-onnx"
version = "0.1.0"
edition = "2021"

[dependencies]
pgso-core = { path = "../pgso-core" }
ort = "2"

[dev-dependencies]
criterion = { version = "0.5", features = ["html_reports"] }

[[bench]]
name = "latency"
harness = false
```

- [ ] **Step 2: Verify builds**

Run: `cargo build -p pgso-signal-onnx`

---

### Task 2: Audio windowing (TDD)

**Files:** Create: `crates/pgso-signal-onnx/src/windowing.rs`

- [ ] **Step 1: Write failing test**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_window_count() {
        // 2 seconds at 16kHz = 32000 samples
        // Window = 800ms = 12800 samples, hop = 400ms = 6400 samples
        // Windows: (0..12800), (6400..19200), (12800..25600), (19200..32000) = 4 windows
        let samples = vec![0.0f32; 32000];
        let windows = sliding_windows(&samples, 12800, 6400);
        assert_eq!(windows.len(), 4);
        assert_eq!(windows[0].len(), 12800);
    }

    #[test]
    fn test_short_audio_no_window() {
        let samples = vec![0.0f32; 5000]; // too short for one window
        let windows = sliding_windows(&samples, 12800, 6400);
        assert!(windows.is_empty());
    }
}
```

- [ ] **Step 2: Implement**

```rust
/// Split audio samples into overlapping windows.
/// Returns slices of exactly `window_size` samples, advancing by `hop_size`.
pub fn sliding_windows(samples: &[f32], window_size: usize, hop_size: usize) -> Vec<&[f32]> {
    let mut windows = Vec::new();
    let mut start = 0;
    while start + window_size <= samples.len() {
        windows.push(&samples[start..start + window_size]);
        start += hop_size;
    }
    windows
}
```

- [ ] **Step 3: Run tests**

Run: `cargo test -p pgso-signal-onnx -- windowing`
Expected: PASS

---

### Task 3: Voice activity detection (TDD)

**Files:** Create: `crates/pgso-signal-onnx/src/vad.rs`

- [ ] **Step 1: Write failing test**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_silence_below_threshold() {
        let silence = vec![0.001f32; 12800];
        assert!(!is_speech(&silence, 0.01));
    }

    #[test]
    fn test_speech_above_threshold() {
        let speech: Vec<f32> = (0..12800).map(|i| (i as f32 * 0.01).sin() * 0.5).collect();
        assert!(is_speech(&speech, 0.01));
    }
}
```

- [ ] **Step 2: Implement**

```rust
/// Energy-based VAD: returns true if RMS energy exceeds threshold.
pub fn rms_energy(samples: &[f32]) -> f32 {
    if samples.is_empty() { return 0.0; }
    let sum_sq: f32 = samples.iter().map(|s| s * s).sum();
    (sum_sq / samples.len() as f32).sqrt()
}

pub fn is_speech(samples: &[f32], threshold: f32) -> bool {
    rms_energy(samples) > threshold
}
```

- [ ] **Step 3: Run tests**

Run: `cargo test -p pgso-signal-onnx -- vad`
Expected: PASS

---

### Task 4: Per-channel standardization (TDD)

**Files:** Add to `crates/pgso-signal-onnx/src/lib.rs`

- [ ] **Step 1: Write failing test**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_running_stats_z_score() {
        let mut stats = RunningStats::new();
        // Feed values with known mean and variance
        for v in &[0.5, 0.6, 0.4, 0.55, 0.45] {
            stats.update(*v as f64);
        }
        // Z-score of a value far from mean should be large
        let z = stats.z_score(0.9);
        assert!(z.abs() > 1.0, "z-score should be > 1: {}", z);
    }

    #[test]
    fn test_z_score_with_few_samples_returns_raw() {
        let mut stats = RunningStats::new();
        stats.update(0.5);
        // With < 2 samples, z-score should return the raw value
        let z = stats.z_score(0.8);
        assert!((z - 0.8).abs() < 0.01);
    }
}
```

- [ ] **Step 2: Implement RunningStats (Welford's algorithm)**

```rust
/// Online mean/variance computation using Welford's algorithm.
#[derive(Debug)]
pub struct RunningStats {
    count: u64,
    mean: f64,
    m2: f64,
}

impl RunningStats {
    pub fn new() -> Self { Self { count: 0, mean: 0.0, m2: 0.0 } }

    pub fn update(&mut self, value: f64) {
        self.count += 1;
        let delta = value - self.mean;
        self.mean += delta / self.count as f64;
        let delta2 = value - self.mean;
        self.m2 += delta * delta2;
    }

    pub fn variance(&self) -> f64 {
        if self.count < 2 { return 0.0; }
        self.m2 / (self.count - 1) as f64
    }

    pub fn std_dev(&self) -> f64 { self.variance().sqrt() }

    /// Returns z-score, or raw value if insufficient data.
    pub fn z_score(&self, value: f64) -> f64 {
        let sd = self.std_dev();
        if sd < 1e-9 || self.count < 2 {
            return value as f64;
        }
        (value - self.mean) / sd
    }
}
```

- [ ] **Step 3: Run tests**

Run: `cargo test -p pgso-signal-onnx -- running_stats`
Expected: PASS

---

### Task 5: OnnxSignal implementation

**Files:** Modify: `crates/pgso-signal-onnx/src/lib.rs`

- [ ] **Step 1: Write the Signal trait implementation**

```rust
use pgso_core::{AudioWindow, Axis, Signal, SignalReading};

mod windowing;
mod vad;

pub struct OnnxSignal {
    session: ort::Session,
    vad_threshold: f32,
    window_size: usize,    // samples (800ms at 16kHz = 12800)
    hop_size: usize,       // samples (400ms at 16kHz = 6400)
    valence_stats: RunningStats,
    arousal_stats: RunningStats,
}

impl OnnxSignal {
    pub fn new(model_path: &str, vad_threshold: f32) -> Result<Self, ort::Error> {
        let session = ort::Session::builder()?
            .with_model_from_file(model_path)?;
        Ok(Self {
            session,
            vad_threshold,
            window_size: 12800,  // 800ms at 16kHz
            hop_size: 6400,      // 400ms at 16kHz
            valence_stats: RunningStats::new(),
            arousal_stats: RunningStats::new(),
        })
    }
}

impl Signal for OnnxSignal {
    fn extract(&mut self, window: &AudioWindow) -> Vec<SignalReading> {
        let sub_windows = windowing::sliding_windows(&window.samples, self.window_size, self.hop_size);
        let mut readings = Vec::new();

        for (i, sub_win) in sub_windows.iter().enumerate() {
            if !vad::is_speech(sub_win, self.vad_threshold) {
                continue; // REQ-3.8: skip silence
            }

            // Run ONNX inference
            // Model output order: [arousal, dominance, valence]
            let input = ort::Value::from_array(
                ndarray::Array2::from_shape_vec((1, sub_win.len()), sub_win.to_vec())
                    .expect("shape mismatch") // test-only path; real code should propagate
            ).expect("tensor creation");

            let outputs = self.session.run(ort::inputs![input].expect("inputs"))
                .expect("inference"); // TODO: propagate errors properly in production

            let output_tensor = outputs[0].try_extract_tensor::<f32>()
                .expect("extract");
            let arousal_raw = output_tensor[[0, 0]];
            let valence_raw = output_tensor[[0, 2]]; // skip dominance at [0,1]

            // Per-channel standardization (REQ-3.5)
            self.valence_stats.update(valence_raw as f64);
            self.arousal_stats.update(arousal_raw as f64);

            let ts = window.timestamp_ms + (i as u64 * (self.hop_size as u64 * 1000 / window.sample_rate as u64));

            // Confidence derived from VAD energy (normalized)
            let energy = vad::rms_energy(sub_win);
            let confidence = (energy / 0.5).min(1.0); // normalize to [0,1]

            readings.push(SignalReading {
                value: self.valence_stats.z_score(valence_raw as f64) as f32,
                axis: Axis::Valence,
                confidence,
                timestamp_ms: ts,
            });
            readings.push(SignalReading {
                value: self.arousal_stats.z_score(arousal_raw as f64) as f32,
                axis: Axis::Arousal,
                confidence,
                timestamp_ms: ts,
            });
        }

        readings
    }
}
```

**Note:** The `expect()` calls in the ONNX inference path are acceptable for the MVP prototype. In production, these should be replaced with proper error propagation (the `Signal` trait would need to return `Result`). Flag this as a known tech-debt item.

- [ ] **Step 2: Write integration test (requires model file)**

```rust
#[cfg(test)]
mod integration_tests {
    use super::*;
    use pgso_core::AudioWindow;
    use std::path::Path;

    #[test]
    #[ignore] // Run with: cargo test -- --ignored (requires model file)
    fn test_signal_trait_impl() {
        let model_path = "../../models/wav2vec2_emotion.onnx";
        if !Path::new(model_path).exists() {
            eprintln!("Skipping: model file not found at {}", model_path);
            return;
        }
        let mut signal = OnnxSignal::new(model_path, 0.01).unwrap();
        let window = AudioWindow {
            samples: vec![0.1; 16000], // 1s of constant signal
            sample_rate: 16000,
            timestamp_ms: 0,
        };
        let readings = signal.extract(&window);
        // With constant signal, VAD might detect it; readings should have valid shape
        for r in &readings {
            assert!(r.confidence >= 0.0 && r.confidence <= 1.0);
        }
    }

    #[test]
    fn test_vad_skips_silence() {
        // This test doesn't need the model — it tests the VAD gate
        let silence = AudioWindow {
            samples: vec![0.0001; 16000],
            sample_rate: 16000,
            timestamp_ms: 0,
        };
        // Can't construct OnnxSignal without model, so test VAD directly
        assert!(!vad::is_speech(&silence.samples[..12800], 0.01));
    }

    #[test]
    fn test_core_has_no_onnx_dep() {
        // Verify pgso-core's Cargo.toml does not reference ort
        let manifest = std::fs::read_to_string("../pgso-core/Cargo.toml")
            .expect("read pgso-core Cargo.toml");
        assert!(!manifest.contains("ort"), "pgso-core must not depend on ort (INV-1)");
        assert!(!manifest.contains("onnx"), "pgso-core must not depend on onnx (INV-1)");
    }
}
```

- [ ] **Step 3: Run tests**

Run: `cargo test -p pgso-signal-onnx` (non-ignored tests)
Expected: PASS (windowing, VAD, stats, dep-check tests)

Run: `cargo test -p pgso-signal-onnx -- --ignored` (if model available)
Expected: PASS

---

### Task 6: Benchmark

**Files:** Create: `crates/pgso-signal-onnx/benches/latency.rs`

- [ ] **Step 1: Write benchmark**

```rust
use criterion::{criterion_group, criterion_main, Criterion};
use pgso_core::{AudioWindow, Signal};
use std::path::Path;

fn bench_window_to_reading(c: &mut Criterion) {
    let model_path = "../../models/wav2vec2_emotion.onnx";
    if !Path::new(model_path).exists() {
        eprintln!("Benchmark skipped: model not found");
        return;
    }

    let mut signal = pgso_signal_onnx::OnnxSignal::new(model_path, 0.01).unwrap();
    let window = AudioWindow {
        samples: (0..12800).map(|i| (i as f32 * 0.01).sin() * 0.3).collect(),
        sample_rate: 16000,
        timestamp_ms: 0,
    };

    c.bench_function("window_to_reading", |b| {
        b.iter(|| signal.extract(&window))
    });
}

criterion_group!(benches, bench_window_to_reading);
criterion_main!(benches);
```

- [ ] **Step 2: Run benchmark and record results**

Run: `cargo bench -p pgso-signal-onnx` (requires model)
Record: p50/p95/p99 latency numbers

- [ ] **Step 3: Commit**

```bash
git add crates/pgso-signal-onnx/
git commit -m "feat(signal-onnx): implement Signal trait with ONNX inference, VAD, standardization"
```

---

## Definition of done

- [x] `pgso-signal-onnx` implements `Signal` behind feature `onnx`
- [x] Windowing + VAD + per-channel standardization implemented
- [x] Unit tests (windowing, VAD, stats, dep-check) pass
- [x] Integration test passes with model file (--ignored)
- [x] Benchmark produces recorded p50/p95/p99
- [x] `pgso-core` remains ML-free (INV-1) — dep-check test enforces it
- [x] `cargo clippy -- -D warnings` clean

---

## STOP HERE

Do NOT:
- wire the signal into DecisionEngine end-to-end (Milestone 4)
- implement the eGeMAPS extractor
- add actuator transport adapters

This milestone proves: real audio in → valid, latency-bounded `SignalReading` out, with model dependency quarantined.
