# pgso-signal-egemaps

The **default `Signal`** for [PGSO](../../README.md): a pure-Rust, eGeMAPS-style
DSP extractor. From a raw 16 kHz mono audio window it computes low-level
prosodic descriptors — F0 (mean, std), RMS energy, jitter, shimmer, voicing —
and maps them to an **arousal** `SignalReading` with a calibrated confidence. No
model, no download, CPU-only, and every reading is **explainable** (*"pitch rose,
energy fell vs. baseline"*).

## Where it sits

Implements `pgso_core::Signal`; plugs into the deterministic core behind that
trait. Swappable — a stronger extractor can replace it without touching the core.

## Scope & signal status (read this)

This extractor is **lightweight and auditable**, not a finished perception
system. Its signal quality is **characterized, not solved**:

- It derives **arousal only** (energy + pitch dynamism); it does not produce
  valence.
- It is **not** validated on spontaneous speech as a governance-grade signal.
  Internal characterization on *acted* speech (MUStARD++) placed eGeMAPS arousal
  vs. human arousal in the **WEAK** band (Spearman ρ ≈ 0.21), and that weakness
  was found to be **inherent** to the two-feature mapping — label-free
  recalibration did not move it. Spontaneous-speech validation is future work.

Use it as the auditable **default and reference floor**; swap in a stronger,
domain-validated `Signal` when you have one.

## Usage

```rust
use pgso_core::AudioWindow;
use pgso_signal_egemaps::EgemapsSignal;

let mut signal = EgemapsSignal::new(16_000); // construction sample rate (Hz)
for e in signal.extract_explained(&window) {
    println!("axis={:?} value={:.3} confidence={:.3}  (mean_f0={:.0}Hz energy={:.3})",
        e.reading.axis, e.reading.value, e.reading.confidence,
        e.features.mean_f0_hz, e.features.mean_energy);
}
```

Runnable demos (no model download, CPU-only):

```bash
cargo run -p pgso-signal-egemaps --example extract      # one window -> features behind the reading
cargo run -p pgso-signal-egemaps --example governance   # end-to-end: prosody prunes a tool, calm restores it
cargo bench -p pgso-signal-egemaps                       # signal latency p50/p95/p99
```
