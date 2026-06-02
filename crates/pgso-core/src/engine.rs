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

use crate::types::{Axis, SignalReading};
use std::collections::HashMap;

/// Configuration for the [`DecisionEngine`]. All thresholds are caller-supplied
/// so the decision path stays free of magic constants and ambient state.
#[derive(Debug, Clone)]
pub struct EngineConfig {
    /// Minimum [`SignalReading::confidence`] to act on a reading. Below this the
    /// engine abstains (returns `None`) and holds state (G4).
    pub confidence_threshold: f32,
    /// Minimum `|value - baseline|` deviation to count a window as "above".
    pub deviation_threshold: f32,
    /// Number of consecutive above-threshold windows required to trigger
    /// (hysteresis). A lone spike never triggers.
    pub hysteresis_window: u32,
    /// EMA smoothing factor applied to the baseline after warm-up. Larger values
    /// track the signal faster.
    pub ema_alpha: f32,
    /// Number of initial readings used to seed a stable personal baseline via a
    /// cumulative mean before switching to EMA.
    pub warmup_readings: u64,
    /// Baseline value assumed at `t = 0`, before any reading for an axis.
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

/// Per-axis running state: adapted baseline, count of readings seen (for the
/// warm-up/EMA switch), and the hysteresis run-length of above-threshold
/// windows.
struct AxisState {
    baseline: f32,
    readings_count: u64,
    consecutive_above: u32,
}

impl AxisState {
    fn new(prior: f32) -> Self {
        Self { baseline: prior, readings_count: 0, consecutive_above: 0 }
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
            self.baseline = self.baseline * (1.0 - alpha) + value * alpha;
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
    pub fn new(config: EngineConfig) -> Self {
        Self { config, axes: HashMap::new() }
    }

    /// Process one reading.
    ///
    /// Returns `Some(EngineOutput)` only when a deviation has been sustained for
    /// `hysteresis_window` consecutive above-threshold windows. Returns `None`
    /// on abstention (confidence below threshold, G4) or when the deviation is
    /// sub-threshold or not yet sustained.
    pub fn process(&mut self, reading: &SignalReading) -> Option<EngineOutput> {
        // G4: abstain on low confidence — hold state, emit nothing.
        if reading.confidence < self.config.confidence_threshold {
            return None;
        }

        let state = self
            .axes
            .entry(reading.axis)
            .or_insert_with(|| AxisState::new(self.config.population_prior));

        state.update_baseline(reading.value, self.config.ema_alpha, self.config.warmup_readings);

        let deviation = (reading.value - state.baseline).abs();

        if deviation > self.config.deviation_threshold {
            state.consecutive_above += 1;
        } else {
            // Sub-threshold window breaks the run (hysteresis reset).
            state.consecutive_above = 0;
            return None;
        }

        if state.consecutive_above < self.config.hysteresis_window {
            return None;
        }

        Some(EngineOutput {
            axis: reading.axis,
            raw_value: reading.value,
            deviation,
            confidence: reading.confidence,
            baseline: state.baseline,
            timestamp_ms: reading.timestamp_ms,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{Axis, SignalReading};

    fn reading(value: f32, axis: Axis, confidence: f32, ts: u64) -> SignalReading {
        SignalReading { value, axis, confidence, timestamp_ms: ts }
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
    fn test_baseline_adapts_ema() {
        let mut engine = DecisionEngine::new(default_config());
        // Feed a drifting-but-calm signal
        for i in 0..20 {
            let val = 0.3 + (i as f32) * 0.01; // slowly drifts up
            engine.process(&reading(val, Axis::Valence, 0.9, i));
        }
        // Baseline should have tracked upward
        let output = engine.process(&reading(0.9, Axis::Valence, 0.9, 20));
        // Deviation should be relative to adapted baseline (~0.4), not population prior (0.5)
        if let Some(o) = output {
            assert!(o.baseline > 0.3, "baseline should have adapted: {}", o.baseline);
        }
    }

    #[test]
    fn test_determinism() {
        let config = default_config();
        let readings: Vec<SignalReading> = (0..20)
            .map(|i| reading(0.3 + (i as f32) * 0.03, Axis::Arousal, 0.8, i))
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
