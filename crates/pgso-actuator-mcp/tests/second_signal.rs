//! Second-`Signal` agnosticism test (REQ-5.5): a synthetic, non-prosodic
//! [`MockLatencySignal`] drives the unchanged `pgso-core` pipeline, proving the
//! `Signal` boundary admits more than prosody.
//!
//! This test deliberately uses `LocalActuator` (not `McpActuator`) — the point
//! here is the *signal* axis of agnosticism, mirroring the M4 `PoC` actuator. It
//! lives in `pgso-actuator-mcp/tests/` (with `pgso-actuator-local` as a
//! dev-dependency of THIS crate) so that `pgso-core` — including its
//! `Cargo.toml` — stays 100% untouched for the M5 diff.
//!
//! `.unwrap()` is permitted here: integration tests compile under the test cfg
//! (master spec §5).

use pgso_actuator_local::LocalActuator;
use pgso_core::{
    pgso_rules, Action, AudioWindow, Axis, Catalog, DecisionEngine, EngineConfig, Pgso, RuleEngine,
    Signal, SignalReading, Tool, ToolId,
};
use std::collections::{HashSet, VecDeque};

/// A mock [`Signal`] simulating response-latency as a governance signal, mapped
/// onto [`Axis::Arousal`]. Proves the [`Signal`] trait admits sources other than
/// prosody (REQ-5.5) with zero changes to `pgso-core`.
struct MockLatencySignal {
    latency_readings: VecDeque<f32>,
}

impl MockLatencySignal {
    fn new(readings: Vec<f32>) -> Self {
        Self { latency_readings: VecDeque::from(readings) }
    }
}

impl Signal for MockLatencySignal {
    fn extract(&mut self, window: &AudioWindow) -> Vec<SignalReading> {
        if let Some(latency) = self.latency_readings.pop_front() {
            vec![SignalReading {
                value: latency,
                // Latency maps onto an arousal-like dimension.
                axis: Axis::Arousal,
                confidence: 0.9,
                timestamp_ms: window.timestamp_ms,
            }]
        } else {
            vec![]
        }
    }
}

fn dummy_window(ts: u64) -> AudioWindow {
    AudioWindow { samples: vec![0.0; 12800], sample_rate: 16000, timestamp_ms: ts }
}

/// REQ-5.5: the mock latency signal, plugged into the unchanged pipeline, drives
/// governance (a step-up on `close_sale`) purely through the `Signal` boundary.
#[test]
fn test_mock_second_signal_accepted() {
    let protected = HashSet::from([ToolId::from("escalate")]);
    let rules = pgso_rules! {
        rule "high_latency" {
            axis: Arousal,
            deviation: 0.3,
            confidence: 0.5,
            action: Action::RequireStepUp(ToolId::from("close_sale"))
        }
    };

    let catalog = Catalog::new(vec![
        Tool::new("search", "Web Search"),
        Tool::new("close_sale", "Close Sale"),
        Tool::new("escalate", "Escalate to Human"),
    ]);

    // Calm latency (5-window warm-up) then 3 windows of sustained high latency.
    let mut latencies = vec![0.5; 5];
    latencies.extend([0.95; 3]);

    let signal = MockLatencySignal::new(latencies);
    let actuator = LocalActuator::new(catalog, protected.clone());
    let config = EngineConfig {
        confidence_threshold: 0.5,
        deviation_threshold: 0.3,
        hysteresis_window: 3,
        ema_alpha: 0.1,
        warmup_readings: 5,
        population_prior: 0.5,
    };

    let mut pgso = Pgso::builder()
        .signal(signal)
        .actuator(actuator)
        .engine(DecisionEngine::new(config))
        .rules(RuleEngine::new(rules, protected))
        .build()
        .unwrap();

    for i in 0..8 {
        pgso.process_window(&dummy_window(i)).unwrap();
    }

    // The non-prosodic latency signal drove a step-up: the Signal boundary
    // accepted a brand-new source with no core change.
    let catalog = pgso.current_catalog();
    let tool = catalog.find(&ToolId::from("close_sale")).unwrap();
    assert!(
        tool.requires_step_up,
        "latency signal should trigger step-up on close_sale"
    );
    // G2 still holds: the protected tool is untouched throughout.
    assert!(
        catalog.contains(&ToolId::from("escalate")),
        "protected tool must survive (G2)"
    );
}
