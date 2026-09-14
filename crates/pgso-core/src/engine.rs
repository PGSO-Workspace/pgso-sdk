//! The [`DecisionEngine`]: a pure, deterministic perception-to-deviation core.
//!
//! It consumes raw [`SignalReading`]s one at a time, maintains a per-[`Axis`]
//! three-layer speaker baseline (population prior → cumulative-mean warm-up →
//! EMA), computes `deviation = |value - baseline|`, applies hysteresis (N
//! consecutive above-threshold windows required), and abstains below the
//! confidence threshold (G4).
//!
//! Determinism (INV-3) is by construction: no wall-clock, no RNG, no ambient
//! state. Timestamps are taken from the caller-supplied
//! [`SignalReading::timestamp_ms`].

// The baseline's cumulative-mean/EMA math converts small reading counts (u64) to
// float; the precision loss is expected and bounded.
#![allow(clippy::cast_precision_loss)]

use crate::types::{Axis, SignalReading};
use std::collections::HashMap;

/// Configuration for the [`DecisionEngine`]. All thresholds are caller-supplied
/// so the decision path stays free of magic constants and ambient state.
///
/// These values are set by the SDK integrator in code (not from untrusted
/// input). [`DecisionEngine::new`] `debug_assert!`s the ranges below so a
/// misconfiguration is caught loudly in dev/test builds.
#[derive(Debug, Clone)]
pub struct EngineConfig {
    /// Minimum [`SignalReading::confidence`] to act on a reading. Below this the
    /// engine abstains (returns `None`) and holds state (G4). Expected `[0, 1]`.
    pub confidence_threshold: f32,
    /// Minimum `|value - baseline|` deviation (inclusive) to count a window as
    /// "above". Expected finite and non-negative.
    pub deviation_threshold: f32,
    /// Number of consecutive above-threshold windows required to trigger
    /// (hysteresis). A lone spike never triggers. A value of `0` behaves
    /// identically to `1` (trigger on the first above-threshold window).
    pub hysteresis_window: u32,
    /// EMA smoothing factor applied to the baseline after warm-up. Larger values
    /// track the signal faster. MUST be in `[0, 1]`; values outside this range
    /// make the baseline diverge or oscillate.
    pub ema_alpha: f32,
    /// Number of initial readings used to seed a stable personal baseline via a
    /// cumulative mean before switching to EMA.
    pub warmup_readings: u64,
    /// Baseline value assumed at `t = 0`, before any reading for an axis. This is
    /// the cold-start reference the first reading's deviation is measured against
    /// (see [`DecisionEngine::process`]). Expected in the signal's value range.
    pub population_prior: f32,
}

/// Output of the [`DecisionEngine`] when a sustained deviation is detected.
///
/// `None` from [`DecisionEngine::process`] means abstain (G4) or not sustained
/// (hysteresis not yet met).
#[derive(Debug, Clone)]
pub struct EngineOutput {
    /// The axis on which the sustained deviation occurred.
    pub axis: Axis,
    /// The raw reading value that produced this output.
    pub raw_value: f32,
    /// `|raw_value - baseline|` at the moment of triggering.
    pub deviation: f32,
    /// The confidence of the triggering reading.
    pub confidence: f32,
    /// The adapted speaker baseline at the moment of triggering.
    pub baseline: f32,
    /// The caller-supplied timestamp of the triggering reading.
    pub timestamp_ms: u64,
}

/// Internal outcomes retain the distinction erased by the public Option API.
pub(crate) enum ProcessOutcome {
    Abstained,
    Nominal,
    Pending,
    Triggered(EngineOutput),
}

/// Per-axis running state: adapted baseline, count of readings seen (for the
/// warm-up/EMA switch), and the hysteresis run-length of above-threshold
/// windows.
struct AxisState {
    baseline: f32,
    readings_count: u64,
    consecutive_above: u32,
}

impl AxisState {
    const fn new(prior: f32) -> Self {
        Self {
            baseline: prior,
            readings_count: 0,
            consecutive_above: 0,
        }
    }

    /// Advance the three-layer baseline with a new value.
    ///
    /// During warm-up (`readings_count <= warmup`) the baseline is the
    /// cumulative mean of all values seen; afterwards it is an EMA. Both forms
    /// are pure functions of prior state and the new value, so the engine is
    /// deterministic (INV-3).
    fn update_baseline(&mut self, value: f32, alpha: f32, warmup: u64) {
        self.readings_count += 1;
        if self.readings_count <= warmup {
            let n = self.readings_count as f32;
            self.baseline = self.baseline * (n - 1.0) / n + value / n;
        } else {
            self.baseline = self.baseline.mul_add(1.0 - alpha, value * alpha);
        }
    }
}

/// The deterministic decision core (INV-3).
///
/// Construct with [`DecisionEngine::new`] and feed readings via
/// [`DecisionEngine::process`]. State is held per [`Axis`]; the engine reads no
/// clock and uses no randomness.
pub struct DecisionEngine {
    config: EngineConfig,
    axes: HashMap<Axis, AxisState>,
}

impl DecisionEngine {
    /// Create an engine with the given configuration. No per-axis state exists
    /// until the first reading for that axis arrives.
    ///
    /// In debug/test builds the configuration ranges documented on
    /// [`EngineConfig`] are asserted, so a misconfiguration surfaces loudly
    /// rather than silently corrupting the baseline.
    #[must_use]
    pub fn new(config: EngineConfig) -> Self {
        debug_assert!(
            (0.0..=1.0).contains(&config.ema_alpha),
            "EngineConfig.ema_alpha must be in [0, 1], got {}",
            config.ema_alpha
        );
        debug_assert!(
            config.confidence_threshold.is_finite()
                && config.deviation_threshold.is_finite()
                && config.population_prior.is_finite(),
            "EngineConfig thresholds and prior must be finite"
        );
        debug_assert!(
            config.deviation_threshold >= 0.0,
            "EngineConfig.deviation_threshold must be non-negative"
        );
        Self {
            config,
            axes: HashMap::new(),
        }
    }

    /// Process one reading.
    ///
    /// Returns `Some(EngineOutput)` only when a deviation has been sustained for
    /// `hysteresis_window` consecutive above-threshold windows. Returns `None`
    /// on abstention (confidence below threshold, G4) or when the deviation is
    /// sub-threshold or not yet sustained.
    /// Non-finite values and confidence outside `[0, 1]` also abstain without
    /// changing the baseline or hysteresis. `None` does not imply recovery.
    ///
    /// Deviation is measured against the *established* baseline — the value
    /// before this reading is folded in — so the population prior is the genuine
    /// cold-start reference and an anomalous reading is measured against prior
    /// expectation, not against a baseline already pulled toward it. The baseline
    /// is then adapted with this reading (three-layer: prior → cumulative mean →
    /// EMA).
    ///
    /// This is **level-triggered**, not edge-triggered: while a deviation stays
    /// sustained it returns `Some` on *every* reading, and `None` as soon as the
    /// deviation subsides. The end-to-end pipeline (M4) depends on this to keep
    /// governance applied while the signal persists and to restore the catalog
    /// when it returns to nominal.
    pub fn process(&mut self, reading: &SignalReading) -> Option<EngineOutput> {
        match self.process_outcome(reading) {
            ProcessOutcome::Triggered(output) => Some(output),
            _ => None,
        }
    }

    pub(crate) fn process_outcome(&mut self, reading: &SignalReading) -> ProcessOutcome {
        // G4: abstain on low confidence — hold state, emit nothing, and do not
        // adapt the baseline from a reading we don't trust.
        if !reading.value.is_finite()
            || !(0.0..=1.0).contains(&reading.confidence)
            || reading.confidence < self.config.confidence_threshold
        {
            return ProcessOutcome::Abstained;
        }

        let state = self
            .axes
            .entry(reading.axis)
            .or_insert_with(|| AxisState::new(self.config.population_prior));

        // Measure against the established baseline BEFORE adapting it.
        let baseline_ref = state.baseline;
        let deviation = (reading.value - baseline_ref).abs();

        // Then fold this reading into the baseline (warm-up mean → EMA).
        state.update_baseline(
            reading.value,
            self.config.ema_alpha,
            self.config.warmup_readings,
        );

        if deviation >= self.config.deviation_threshold {
            // saturating: a pathological unbroken stream can't wrap the counter.
            state.consecutive_above = state.consecutive_above.saturating_add(1);
        } else {
            // Sub-threshold window breaks the run (hysteresis reset).
            state.consecutive_above = 0;
            return ProcessOutcome::Nominal;
        }

        if state.consecutive_above < self.config.hysteresis_window {
            return ProcessOutcome::Pending;
        }

        ProcessOutcome::Triggered(EngineOutput {
            axis: reading.axis,
            raw_value: reading.value,
            deviation,
            confidence: reading.confidence,
            baseline: baseline_ref,
            timestamp_ms: reading.timestamp_ms,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{Axis, SignalReading};

    fn reading(value: f32, axis: Axis, confidence: f32, ts: u64) -> SignalReading {
        SignalReading {
            value,
            axis,
            confidence,
            timestamp_ms: ts,
        }
    }

    fn default_config() -> EngineConfig {
        EngineConfig {
            confidence_threshold: 0.5,
            deviation_threshold: 0.3,
            hysteresis_window: 3,
            ema_alpha: 0.1,
            warmup_readings: 5,
            population_prior: 0.5,
        }
    }

    #[test]
    fn invalid_readings_preserve_baseline_and_hysteresis() {
        for invalid in [
            reading(f32::NAN, Axis::Arousal, 0.9, 2),
            reading(f32::INFINITY, Axis::Arousal, 0.9, 2),
            reading(0.95, Axis::Arousal, f32::NAN, 2),
            reading(0.95, Axis::Arousal, f32::INFINITY, 2),
            reading(0.95, Axis::Arousal, -0.1, 2),
            reading(0.95, Axis::Arousal, 1.1, 2),
        ] {
            let mut engine = DecisionEngine::new(EngineConfig {
                hysteresis_window: 2,
                ema_alpha: 0.0,
                warmup_readings: 0,
                ..default_config()
            });
            assert!(matches!(
                engine.process_outcome(&reading(0.95, Axis::Arousal, 0.9, 1)),
                ProcessOutcome::Pending
            ));
            assert!(matches!(
                engine.process_outcome(&invalid),
                ProcessOutcome::Abstained
            ));
            let output = engine
                .process(&reading(0.95, Axis::Arousal, 0.9, 3))
                .unwrap();
            assert_eq!(output.baseline, 0.5);
            assert!(matches!(
                engine.process_outcome(&reading(0.5, Axis::Arousal, 0.9, 4)),
                ProcessOutcome::Nominal
            ));
        }
    }

    #[test]
    fn test_low_confidence_abstains() {
        let mut engine = DecisionEngine::new(default_config());
        // High value but low confidence → should abstain
        let r = reading(0.95, Axis::Valence, 0.1, 100);
        assert!(engine.process(&r).is_none());
    }

    #[test]
    fn test_isolated_spike_no_trigger() {
        let mut engine = DecisionEngine::new(default_config());
        // Warm up baseline around 0.5
        for i in 0..5 {
            engine.process(&reading(0.5, Axis::Valence, 0.9, i));
        }
        // Single spike
        engine.process(&reading(0.95, Axis::Valence, 0.9, 10));
        // Drop back
        let result = engine.process(&reading(0.5, Axis::Valence, 0.9, 11));
        assert!(result.is_none());
    }

    #[test]
    fn test_sustained_deviation_triggers() {
        let mut engine = DecisionEngine::new(default_config());
        // Warm up baseline around 0.5
        for i in 0..5 {
            engine.process(&reading(0.5, Axis::Valence, 0.9, i));
        }
        // 3 consecutive high readings (hysteresis_window = 3)
        engine.process(&reading(0.95, Axis::Valence, 0.9, 10));
        engine.process(&reading(0.95, Axis::Valence, 0.9, 11));
        let result = engine.process(&reading(0.95, Axis::Valence, 0.9, 12));
        assert!(result.is_some());
        let output = result.unwrap();
        assert!(output.deviation > 0.3);
    }

    #[test]
    fn test_first_reading_measures_against_prior() {
        // Cold-start: with measure-then-update, the population prior is the
        // reference the first reading deviates from — NOT a structurally-zero
        // deviation. (hysteresis_window = 1 so one sustained-enough reading can
        // trigger.)
        let config = EngineConfig {
            confidence_threshold: 0.5,
            deviation_threshold: 0.3,
            hysteresis_window: 1,
            ema_alpha: 0.1,
            warmup_readings: 5,
            population_prior: 0.5,
        };
        let mut engine = DecisionEngine::new(config);
        let out = engine.process(&reading(0.95, Axis::Valence, 0.9, 0));
        let o = out.expect("first reading far from the prior should be able to trigger");
        assert!(
            (o.deviation - 0.45).abs() < 1e-6,
            "deviation vs prior 0.5 should be 0.45, got {}",
            o.deviation
        );
        assert!(
            (o.baseline - 0.5).abs() < 1e-6,
            "reported baseline should be the prior reference, got {}",
            o.baseline
        );
    }

    #[test]
    fn test_baseline_adapts_to_signal() {
        // The baseline must adapt toward a sustained signal far from the prior,
        // so later readings are measured against the ADAPTED baseline, not the
        // stale prior. Non-vacuous: asserts both that an at-baseline reading does
        // NOT trigger and that a far-from-baseline reading DOES, plus the
        // reported baseline reflects adaptation.
        let config = EngineConfig {
            confidence_threshold: 0.5,
            deviation_threshold: 0.3,
            hysteresis_window: 1,
            ema_alpha: 0.3,
            warmup_readings: 3,
            population_prior: 0.1,
        };
        let mut engine = DecisionEngine::new(config);
        // Converge the baseline near 0.7 (far from prior 0.1).
        for i in 0..15 {
            engine.process(&reading(0.7, Axis::Valence, 0.9, i));
        }
        // A reading AT the adapted baseline must not deviate/trigger.
        assert!(
            engine
                .process(&reading(0.7, Axis::Valence, 0.9, 15))
                .is_none(),
            "a reading at the adapted baseline must not trigger"
        );
        // A reading FAR from the adapted baseline must trigger, and the reported
        // baseline must reflect adaptation (≫ the 0.1 prior).
        let o = engine
            .process(&reading(0.2, Axis::Valence, 0.9, 16))
            .expect("a reading far below the adapted baseline should trigger");
        assert!(
            o.baseline > 0.5,
            "baseline should have adapted near 0.7, got {}",
            o.baseline
        );
    }

    #[test]
    fn test_determinism() {
        let config = default_config();
        let readings: Vec<SignalReading> = (0..20)
            .map(|i| reading((i as f32).mul_add(0.03, 0.3), Axis::Arousal, 0.8, i))
            .collect();

        let mut engine1 = DecisionEngine::new(config.clone());
        let mut engine2 = DecisionEngine::new(config);

        let out1: Vec<_> = readings.iter().map(|r| engine1.process(r)).collect();
        let out2: Vec<_> = readings.iter().map(|r| engine2.process(r)).collect();

        for (a, b) in out1.iter().zip(out2.iter()) {
            match (a, b) {
                (Some(a), Some(b)) => {
                    assert_eq!(a.axis, b.axis);
                    assert!((a.deviation - b.deviation).abs() < f32::EPSILON);
                    assert!((a.baseline - b.baseline).abs() < f32::EPSILON);
                }
                (None, None) => {}
                _ => panic!("determinism violated"),
            }
        }
    }
}
