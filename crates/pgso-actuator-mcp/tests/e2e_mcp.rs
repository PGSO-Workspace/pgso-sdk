//! End-to-end agnosticism test: the Milestone-4 governance scenario re-run with
//! the [`McpActuator`] swapped in for the M4 `LocalActuator` (REQ-5.4, REQ-5.6).
//!
//! The pipeline, engine, rules, and signal are all imported **unchanged** from
//! `pgso-core`; only the actuator differs from the M4 e2e test. The fact that
//! this compiles and produces the same governance behavior — `close_sale`
//! pruned under sustained deviation, `escalate` (protected) surviving — over a
//! different transport is the demonstration that PGSO is transport-agnostic.
//!
//! Lives in `pgso-actuator-mcp/tests/` (NOT `pgso-core/tests/`) so the
//! agnosticism proof holds: `pgso-core` — including its `Cargo.toml` — is 100%
//! untouched by this milestone.
//!
//! `.unwrap()` is used freely: integration tests compile under the test cfg,
//! where the master spec §5 permits it.

use pgso_actuator_mcp::McpActuator;
use pgso_core::{
    pgso_rules, Action, AudioWindow, Axis, Catalog, DecisionEngine, EngineConfig, Pgso, RuleEngine,
    Signal, SignalReading, Tool, ToolId,
};
use std::collections::{HashSet, VecDeque};

/// The same pre-programmed mock signal used by the M4 e2e test: pops one batch
/// of readings per window, yielding silence once exhausted.
struct MockSignal {
    sequence: VecDeque<Vec<SignalReading>>,
}

impl MockSignal {
    fn new(readings: Vec<Vec<SignalReading>>) -> Self {
        Self {
            sequence: VecDeque::from(readings),
        }
    }
}

impl Signal for MockSignal {
    fn extract(&mut self, _window: &AudioWindow) -> Vec<SignalReading> {
        self.sequence.pop_front().unwrap_or_default()
    }
}

fn dummy_window(ts: u64) -> AudioWindow {
    AudioWindow {
        samples: vec![0.0; 12800],
        sample_rate: 16000,
        timestamp_ms: ts,
    }
}

const fn reading(value: f32, axis: Axis, confidence: f32, ts: u64) -> SignalReading {
    SignalReading {
        value,
        axis,
        confidence,
        timestamp_ms: ts,
    }
}

/// The standard four-tool fixture from the master spec §4.6.
fn test_catalog() -> Catalog {
    Catalog::new(vec![
        Tool::new("search", "Web Search"),
        Tool::new("calculate", "Calculator"),
        Tool::new("close_sale", "Close Sale"),
        Tool::new("escalate", "Escalate to Human"),
    ])
}

/// The exact M4 engine configuration.
const fn default_config() -> EngineConfig {
    EngineConfig {
        confidence_threshold: 0.5,
        deviation_threshold: 0.3,
        hysteresis_window: 3,
        ema_alpha: 0.1,
        warmup_readings: 5,
        population_prior: 0.5,
    }
}

/// REQ-5.4 + REQ-5.6: the M4 sustained-deviation scenario, re-run over MCP.
///
/// 5 calm warm-up readings + 3 sustained high readings drive the same `Prune`
/// rule. With the `McpActuator`: `close_sale` must leave both the catalog AND
/// the served `tools/list` payload; `escalate` (protected) must remain in both.
#[test]
fn test_e2e_over_mcp() {
    let protected = HashSet::from([ToolId::from("escalate")]);
    let rules = pgso_rules! {
        rule "high_valence" {
            axis: Valence,
            deviation: 0.3,
            confidence: 0.5,
            action: Action::Prune(ToolId::from("close_sale"))
        }
    };

    let mut readings = Vec::new();
    for i in 0..5 {
        readings.push(vec![reading(0.5, Axis::Valence, 0.9, i)]);
    }
    for i in 5..8 {
        readings.push(vec![reading(0.95, Axis::Valence, 0.9, i)]);
    }

    // The ONLY difference from the M4 e2e test: McpActuator instead of
    // LocalActuator. Everything else is the unchanged pgso-core pipeline.
    let actuator = McpActuator::new(test_catalog(), protected.clone());

    let mut pgso = Pgso::builder()
        .signal(MockSignal::new(readings))
        .actuator(actuator)
        .engine(DecisionEngine::new(default_config()))
        .rules(RuleEngine::new(rules, protected.clone()))
        .build()
        .unwrap();

    for i in 0..8 {
        pgso.process_window(&dummy_window(i)).unwrap();
    }

    // Governance behavior is equivalent to M4 (transport-agnostic): assert on
    // the served catalog, the state the `Pgso` pipeline exposes.
    let catalog = pgso.current_catalog();
    assert!(
        !catalog.contains(&ToolId::from("close_sale")),
        "close_sale should be pruned after sustained high-deviation signal"
    );
    assert!(
        catalog.contains(&ToolId::from("escalate")),
        "escalate must survive (G2 over MCP)"
    );

    // REQ-5.2 / REQ-5.6 at the TRANSPORT boundary: render the served catalog as
    // the MCP tools/list payload. `tools_list_response` is a pure function of
    // the served (active) catalog, so rendering the pipeline's served catalog
    // here yields exactly what the pipeline's internal actuator serves —
    // close_sale absent, escalate present.
    let payload = McpActuator::new(catalog, protected).tools_list_response();
    let tools = payload["tools"].as_array().unwrap();
    // `name` is the programmatic id per the MCP spec (display label is `title`).
    let names: Vec<&str> = tools.iter().map(|t| t["name"].as_str().unwrap()).collect();
    assert_eq!(tools.len(), 3, "MCP tools/list should omit the pruned tool");
    assert!(
        !names.contains(&"close_sale"),
        "REQ-5.2: pruned tool must be absent from the MCP tools/list payload"
    );
    assert!(
        names.contains(&"escalate"),
        "REQ-5.6 (G2 over MCP): protected tool must remain in the MCP payload"
    );
}
