//! Criterion latency benchmark: window-in to reading-out (REQ-3.7).
//!
//! Pure DSP runs in CI with no model file. Criterion reports the median plus a
//! confidence interval; read p50/p95/p99 from the generated report (or the
//! slope/mean lines printed to stdout).

// Synthetic-tone generation converts sample indices/rate between int and float;
// the precision loss is irrelevant to a benchmark fixture.
#![allow(clippy::cast_precision_loss)]

use criterion::{black_box, criterion_group, criterion_main, Criterion};
use pgso_core::{AudioWindow, Signal};
use pgso_signal_egemaps::EgemapsSignal;
use std::f32::consts::PI;

fn voiced_window(sr: u32, secs: f32) -> AudioWindow {
    let len = (sr as f32 * secs) as usize;
    let samples = (0..len)
        .map(|i| 0.4 * (2.0 * PI * 180.0 * i as f32 / sr as f32).sin())
        .collect();
    AudioWindow {
        samples,
        sample_rate: sr,
        timestamp_ms: 0,
    }
}

fn bench_window_to_reading(c: &mut Criterion) {
    let window = voiced_window(16000, 1.0);
    c.bench_function("egemaps_window_to_reading", |b| {
        b.iter(|| {
            let mut sig = EgemapsSignal::new(16000);
            black_box(sig.extract(black_box(&window)))
        });
    });
}

criterion_group!(benches, bench_window_to_reading);
criterion_main!(benches);
