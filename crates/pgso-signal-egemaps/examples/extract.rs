//! The "press go" default-signal demo: synth a voiced tone, extract, print the
//! interpretable features behind each reading. No model download, runs on CPU.

// Synthetic-tone generation converts sample indices/rate between int and float;
// the precision loss is irrelevant to a demo fixture.
#![allow(clippy::cast_precision_loss)]

use pgso_core::AudioWindow;
use pgso_signal_egemaps::EgemapsSignal;
use std::f32::consts::PI;

fn main() {
    let sr = 16000u32;
    let len = sr as usize; // 1 second
    let samples: Vec<f32> = (0..len)
        .map(|i| 0.4 * (2.0 * PI * 180.0 * i as f32 / sr as f32).sin())
        .collect();
    let window = AudioWindow {
        samples,
        sample_rate: sr,
        timestamp_ms: 0,
    };

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
