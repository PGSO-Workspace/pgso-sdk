//! Criterion latency benchmark for the DECISION STEP ONLY.
//!
//! This measures [`DecisionEngine::process`] on a `SignalReading` that is
//! constructed directly — NO eGeMAPS / DSP / model code is on the timed path.
//! `SignalReading` is a plain public struct (`value`, `axis`, `confidence`,
//! `timestamp_ms`), so a representative input is built outside the timing loop
//! without going through any extractor.
//!
//! `process` is `&mut self` and STATEFUL (it adapts a per-axis EMA baseline and a
//! hysteresis run-length), so a naive hot loop on one engine measures a drifting
//! operation that converges to the cheapest branch. We therefore pin the branch
//! being measured:
//!
//!   * `decision_nominal` — the sub-threshold ("nominal speech") path that runs
//!     on virtually every reading. We feed `value == baseline`, so deviation is
//!     0 every call, the engine stays in steady state, and there is no drift.
//!     The engine is pre-warmed past `warmup_readings` so the timed call uses the
//!     production EMA branch, not the cold-start cumulative-mean branch.
//!
//!   * `decision_trigger` — the worst-case path that builds an `EngineOutput`.
//!     A long hot loop here WOULD drift (the EMA pulls the baseline toward the
//!     reading until the deviation falls below threshold), so we use
//!     `iter_batched`: every timed call gets a freshly built engine that is
//!     already in the sustained-trigger state, and runs exactly one `process`.
//!     The engine construction + warm-up is setup cost, excluded from timing.

use criterion::{black_box, criterion_group, criterion_main, BatchSize, Criterion};
use pgso_core::{Axis, DecisionEngine, EngineConfig, SignalReading};

/// Pin the current (measuring) thread to a single logical processor. On the
/// hybrid i7-12650H, logical procs 0..=11 are P-core threads and 12..=15 are
/// E-cores (verified via `GetLogicalProcessorInformationEx`: P-cores report
/// efficiency_class 1, E-cores 0). Override with `PGSO_BENCH_CORE`; default 2 is
/// a P-core thread. Prints the chosen id and whether pinning succeeded.
fn pin_to_pcore() {
    let id: usize = std::env::var("PGSO_BENCH_CORE")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(2);
    let ok = core_affinity::set_for_current(core_affinity::CoreId { id });
    let kind = if id <= 11 { "P-core" } else { "E-core" };
    eprintln!("pin_to_pcore: logical core {id} ({kind}); set_for_current -> {ok}");
}

/// The configuration under test. `EngineConfig` has no `Default`; these are the
/// values used by the engine's own unit tests (`engine.rs::default_config`).
fn config() -> EngineConfig {
    EngineConfig {
        confidence_threshold: 0.5,
        deviation_threshold: 0.3,
        hysteresis_window: 3,
        ema_alpha: 0.1,
        warmup_readings: 5,
        population_prior: 0.5,
    }
}

fn reading(value: f32) -> SignalReading {
    SignalReading {
        value,
        axis: Axis::Arousal,
        confidence: 0.9, // >= confidence_threshold, so we never hit the abstain branch
        timestamp_ms: 0,
    }
}

/// An engine warmed past `warmup_readings` with at-baseline readings, so it sits
/// in the steady-state EMA regime with baseline == population_prior (0.5). A
/// subsequent reading of 0.5 deviates by 0 and takes the sub-threshold branch.
fn warmed_nominal() -> DecisionEngine {
    let mut engine = DecisionEngine::new(config());
    let r = reading(0.5);
    for _ in 0..32 {
        engine.process(&r);
    }
    engine
}

/// An engine driven into the sustained-trigger state: warm-up at the prior, then
/// enough above-threshold readings to satisfy `hysteresis_window`. The NEXT
/// `process(reading(0.95))` returns `Some` (level-triggered) and still deviates
/// >= threshold, so the timed call exercises the full EngineOutput path.
fn warmed_sustained() -> DecisionEngine {
    let mut engine = DecisionEngine::new(config());
    let nominal = reading(0.5);
    for _ in 0..5 {
        engine.process(&nominal); // baseline settles at 0.5
    }
    let high = reading(0.95);
    for _ in 0..3 {
        engine.process(&high); // consecutive_above reaches hysteresis_window
    }
    engine
}

fn bench_decision(c: &mut Criterion) {
    pin_to_pcore();

    // Inputs built ONCE, outside every timing loop.
    let nominal = reading(0.5);
    let high = reading(0.95);

    // Common case: sub-threshold, stable, no drift.
    let mut engine = warmed_nominal();
    c.bench_function("decision_nominal", |b| {
        b.iter(|| black_box(engine.process(black_box(&nominal))));
    });

    // Worst case: full trigger path, fresh sustained engine per timed call.
    c.bench_function("decision_trigger", |b| {
        b.iter_batched(
            warmed_sustained,
            |mut engine| black_box(engine.process(black_box(&high))),
            BatchSize::SmallInput,
        );
    });
}

criterion_group!(benches, bench_decision);
criterion_main!(benches);
