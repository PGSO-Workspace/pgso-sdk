# pgso-signal-egemaps

The **default `Signal`** for [PGSO](../../README.md): a pure-Rust, eGeMAPS-style
DSP extractor. From a raw 16 kHz mono audio window it computes low-level
prosodic descriptors — F0 (mean, std), RMS energy, jitter, shimmer, voicing —
and maps energy and pitch variability to a heuristic **arousal** `SignalReading`.
Its confidence is a voicing-quality heuristic, not a calibrated probability of
emotion or an appropriate governance action. It runs on CPU without a model
download, and exposes the descriptors behind each reading.

## Where it sits

Implements `pgso_core::Signal`; plugs into the deterministic core behind that
trait. Swappable — a stronger extractor can replace it without touching the core.

## Scope & signal status (read this)

This extractor is **lightweight and auditable**, not a finished perception
system. Its signal quality is **characterized, not solved**:

- It derives **arousal only** (energy + pitch dynamism); it does not produce
  valence.
- It is **not** validated on spontaneous speech as a governance-grade signal.
  Historical acted-speech characterizations and their reproducibility limits are
  documented in the [experimental package](../../experiments/reproducibility/README.md).
  Those reports do not establish signal validity for a new population or task.

Use it as a reference extractor. Any interpretation of its readings and any
replacement `Signal` require validation for the intended domain.

## Usage

```rust
use pgso_core::AudioWindow;
use pgso_signal_egemaps::EgemapsSignal;

let window = AudioWindow {
    samples: vec![0.0; 16_000], // one second of silence; may yield no readings
    sample_rate: 16_000,
    timestamp_ms: 0,
};
let mut signal = EgemapsSignal::new(16_000);
for e in signal.extract_explained(&window) {
    println!("axis={:?} value={:.3} confidence={:.3}  (mean_f0={:.0}Hz energy={:.3})",
        e.reading.axis, e.reading.value, e.reading.confidence,
        e.features.mean_f0_hz, e.features.mean_energy);
}
```

Runnable demos (no model download, CPU-only):

```bash
cargo run -p pgso-signal-egemaps --example extract      # one window -> features behind the reading
cargo run -p pgso-signal-egemaps --example governance   # synthetic catalog-policy demonstration
cargo bench -p pgso-signal-egemaps                       # signal latency p50/p95/p99
```
