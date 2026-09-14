//! Percentiles of BATCH-MEAN cost for the DECISION STEP ONLY.
//! These are not individual-call tail latencies or end-to-end agent latency.
//!
//! Criterion reports mean + CI; this reports p50/p95/p99/p99.9/max. It measures
//! the SAME operation as the `decision_nominal` criterion benchmark: one
//! `DecisionEngine::process` call on the steady-state sub-threshold path (the
//! path that runs on virtually every reading). The `SignalReading` is built
//! directly — no eGeMAPS / DSP / model code is on the timed path.
//!
//! `Instant::now()` has ~20-40 ns of its own overhead, which would dominate a
//! sub-nanosecond-to-few-nanosecond operation. We amortize it: each sample times
//! a BATCH of `process` calls with a SINGLE `Instant::now()` pair and divides the
//! elapsed time by BATCH. Because `value == baseline` the engine stays in steady
//! state, so every call in the batch is the same operation (no `&mut` drift).

use pgso_core::{Axis, DecisionEngine, EngineConfig, SignalReading};
use std::hint::black_box;
use std::time::Instant;

const BATCH: usize = 1_000;
const SAMPLES: usize = 100_000;
const WARMUP: usize = 10_000;

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

/// Nearest-rank percentile over an already-sorted ascending slice.
fn percentile(sorted: &[f64], p: f64) -> f64 {
    debug_assert!(!sorted.is_empty());
    let n = sorted.len();
    // rank in [1, n]; index in [0, n-1]
    let rank = (p / 100.0 * n as f64).ceil() as usize;
    let idx = rank.saturating_sub(1).min(n - 1);
    sorted[idx]
}

/// Pin the measuring thread to a single logical processor. On the hybrid
/// i7-12650H, logical procs 0..=11 are P-core threads and 12..=15 are E-cores
/// (verified via `GetLogicalProcessorInformationEx`: P-cores report
/// efficiency_class 1, E-cores 0). Override with `PGSO_BENCH_CORE`; default 2 is
/// a P-core thread.
fn pin_to_pcore() {
    let id: usize = std::env::var("PGSO_BENCH_CORE")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(2);
    let ok = core_affinity::set_for_current(core_affinity::CoreId { id });
    eprintln!(
        "CPU affinity: logical core {id}; set_for_current -> {ok}; core type is host-dependent"
    );
}

fn main() {
    pin_to_pcore();

    // Built ONCE, outside the timing loop.
    let nominal = SignalReading {
        value: 0.5, // == population_prior == steady-state baseline -> sub-threshold
        axis: Axis::Arousal,
        confidence: 0.9,
        timestamp_ms: 0,
    };

    let mut engine = DecisionEngine::new(config());
    // Warm past warmup_readings so we measure the production EMA branch; baseline
    // settles at 0.5, so deviation stays 0 (sub-threshold) for every later call.
    for _ in 0..64 {
        engine.process(&nominal);
    }

    // Untimed warm-up: let the CPU reach steady state (caches, frequency).
    for _ in 0..WARMUP {
        for _ in 0..BATCH {
            black_box(engine.process(black_box(&nominal)));
        }
    }

    let mut ns_per_call: Vec<f64> = Vec::with_capacity(SAMPLES);
    for _ in 0..SAMPLES {
        let start = Instant::now();
        for _ in 0..BATCH {
            black_box(engine.process(black_box(&nominal)));
        }
        let elapsed = start.elapsed();
        ns_per_call.push(elapsed.as_nanos() as f64 / BATCH as f64);
    }

    ns_per_call.sort_by(|a, b| a.partial_cmp(b).expect("no NaN timings"));

    let n = ns_per_call.len() as f64;
    let mean = ns_per_call.iter().sum::<f64>() / n;
    let p50 = percentile(&ns_per_call, 50.0);
    let p95 = percentile(&ns_per_call, 95.0);
    let p99 = percentile(&ns_per_call, 99.0);
    let p999 = percentile(&ns_per_call, 99.9);
    let max = *ns_per_call.last().expect("non-empty");

    println!("decision-step batch-mean cost (not individual-call tail latency)");
    println!(
        "BATCH={BATCH}  SAMPLES={SAMPLES}  WARMUP={WARMUP}  (one Instant::now() per batch, divided by BATCH)"
    );
    println!();
    println!("  stat     ns/call        ms/call");
    println!("  ----    ----------    ------------");
    let row = |label: &str, ns: f64| {
        println!("  {label:<5}  {ns:>9.3}    {:>12.9}", ns / 1.0e6);
    };
    row("mean", mean);
    row("p50", p50);
    row("p95", p95);
    row("p99", p99);
    row("p99.9", p999);
    row("max", max);
}
