# Milestone 5 — Transport Agnosticism (proven, not claimed)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Demonstrate empirically that PGSO's core is agnostic to transport and signal source. Add a second `Actuator` (MCP) and a mock second `Signal`, show the same core runs identically without changes. This converts "agnostic" from a claim into a demonstrated property.

**Architecture:** `pgso-actuator-mcp` implements `Actuator` using MCP `tools/list` payloads. A `MockLatencySignal` implements `Signal` with synthetic data. Both plug into `Pgso<S, A>` with zero changes to `pgso-core`. The diff is the proof.

**Tech Stack:** Rust 2021, pgso-core, serde, serde_json

**Depends on:** Milestone 4 DONE.

---

## Prerequisites (R8: verify before coding)

Before writing ANY code, verify the exact shape of MCP's tool listing:

1. Check the MCP specification for `tools/list` response format
2. Check `notifications/tools/list_changed` notification format
3. If the shape differs from assumptions below, adjust the adapter design FIRST

Expected MCP `tools/list` response shape:
```json
{
  "tools": [
    {
      "name": "tool_name",
      "description": "...",
      "inputSchema": { ... }
    }
  ]
}
```

---

## File structure

```
crates/pgso-actuator-mcp/
├── Cargo.toml                         # (CREATE)
└── src/
    └── lib.rs                         # (CREATE) McpActuator + tests
crates/pgso-core/
└── tests/
    └── e2e_mcp.rs                     # (CREATE) E2E test with MCP adapter
```

---

## Requirements (EARS form)

**REQ-5.1** THE `pgso-actuator-mcp` crate SHALL implement `Actuator` from `pgso-core`.

**REQ-5.2** WHEN a `ScopeDecision` prunes a tool, THE MCP adapter SHALL produce a `tools/list` JSON payload that omits that tool.

**REQ-5.3** THE `pgso-core` crate SHALL require ZERO changes. The diff for this milestone MUST NOT touch `pgso-core/src/`. **This is the agnosticism proof.**

**REQ-5.4** THE same E2E scenario from M4 SHALL run against the MCP adapter, producing the same governance behavior.

**REQ-5.5** A mock second `Signal` implementation SHALL be accepted by `Pgso` without core changes, proving the `Signal` boundary admits more than prosody.

**REQ-5.6 (G2 over MCP)** WHEN a rule targets a protected tool, the MCP-served tool list SHALL still contain that tool.

---

## Tasks

### Task 1: Set up pgso-actuator-mcp crate

**Files:** Create: `crates/pgso-actuator-mcp/Cargo.toml`

- [ ] **Step 1: Create Cargo.toml**

```toml
[package]
name = "pgso-actuator-mcp"
version = "0.1.0"
edition = "2021"

[dependencies]
pgso-core = { path = "../pgso-core" }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

- [ ] **Step 2: Verify**

Run: `cargo build -p pgso-actuator-mcp`

---

### Task 2: McpActuator (TDD)

**Files:** Create: `crates/pgso-actuator-mcp/src/lib.rs`

- [ ] **Step 1: Write failing tests**

```rust
use std::collections::HashSet;
use pgso_core::*;
use serde_json::Value;

// McpActuator will go here

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> McpActuator {
        let tools = vec![
            Tool::new("search", "Web Search"),
            Tool::new("calculate", "Calculator"),
            Tool::new("close_sale", "Close Sale"),
            Tool::new("escalate", "Escalate to Human"),
        ];
        let protected = HashSet::from([ToolId::from("escalate")]);
        McpActuator::new(Catalog::new(tools), protected)
    }

    #[test]
    fn test_mcp_adapter_implements_actuator() {
        let act: Box<dyn Actuator> = Box::new(fixture());
        assert_eq!(act.current_catalog().len(), 4);
    }

    #[test]
    fn test_mcp_prune_omits_tool_from_list() {
        let mut act = fixture();
        act.apply(&ScopeDecision::new(Action::Prune(ToolId::from("close_sale")))).unwrap();

        let payload = act.tools_list_response();
        let tools = payload["tools"].as_array().unwrap();
        let names: Vec<&str> = tools.iter()
            .map(|t| t["name"].as_str().unwrap())
            .collect();
        assert!(!names.contains(&"Close Sale"));
        assert_eq!(tools.len(), 3);
    }

    #[test]
    fn test_mcp_protected_tool_survives() {
        let mut act = fixture();
        act.apply(&ScopeDecision::new(Action::Prune(ToolId::from("escalate")))).unwrap();

        let payload = act.tools_list_response();
        let tools = payload["tools"].as_array().unwrap();
        let names: Vec<&str> = tools.iter()
            .map(|t| t["name"].as_str().unwrap())
            .collect();
        assert!(names.contains(&"Escalate to Human"), "G2: protected tool must survive");
    }

    #[test]
    fn test_mcp_allow_restores_full_list() {
        let mut act = fixture();
        act.apply(&ScopeDecision::new(Action::Prune(ToolId::from("close_sale")))).unwrap();
        act.apply(&ScopeDecision::new(Action::Allow)).unwrap();

        let payload = act.tools_list_response();
        let tools = payload["tools"].as_array().unwrap();
        assert_eq!(tools.len(), 4);
    }

    #[test]
    fn test_mcp_has_changed_flag() {
        let mut act = fixture();
        assert!(!act.has_changed());
        act.apply(&ScopeDecision::new(Action::Prune(ToolId::from("close_sale")))).unwrap();
        assert!(act.has_changed());
        act.acknowledge_change();
        assert!(!act.has_changed());
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cargo test -p pgso-actuator-mcp`
Expected: FAIL

- [ ] **Step 3: Implement McpActuator**

```rust
use std::collections::HashSet;
use pgso_core::{Action, Actuator, ActuatorError, Catalog, ScopeDecision, Tool, ToolId};
use serde_json::{json, Value};

pub struct McpActuator {
    base_catalog: Catalog,
    active_catalog: Catalog,
    protected: HashSet<ToolId>,
    changed: bool,
    directive_blocks: Vec<String>,
}

impl McpActuator {
    pub fn new(catalog: Catalog, protected: HashSet<ToolId>) -> Self {
        Self {
            active_catalog: catalog.clone(),
            base_catalog: catalog,
            protected,
            changed: false,
            directive_blocks: Vec::new(),
        }
    }

    /// Produce the MCP tools/list response payload.
    pub fn tools_list_response(&self) -> Value {
        let tools: Vec<Value> = self.active_catalog.tools().iter().map(|t| {
            json!({
                "name": t.name,
                "description": format!("Tool: {}", t.id.as_str()),
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            })
        }).collect();
        json!({ "tools": tools })
    }

    /// Check if the catalog changed since last acknowledgment
    /// (for notifications/tools/list_changed).
    pub fn has_changed(&self) -> bool {
        self.changed
    }

    /// Acknowledge the change (after sending notification).
    pub fn acknowledge_change(&mut self) {
        self.changed = false;
    }
}

impl Actuator for McpActuator {
    fn current_catalog(&self) -> Catalog {
        self.active_catalog.clone()
    }

    fn apply(&mut self, decision: &ScopeDecision) -> Result<Catalog, ActuatorError> {
        let prev = self.active_catalog.clone();

        match &decision.action {
            Action::Allow => {
                self.active_catalog = self.base_catalog.clone();
                self.directive_blocks.clear();
            }
            Action::Prune(id) => {
                if !self.protected.contains(id) {
                    self.active_catalog.remove(id);
                }
            }
            Action::RequireStepUp(id) => {
                self.active_catalog.set_step_up(id, true);
            }
            Action::InjectDirective(text) => {
                self.directive_blocks.push(text.clone());
            }
        }

        if self.active_catalog != prev {
            self.changed = true;
        }

        Ok(self.active_catalog.clone())
    }
}
```

- [ ] **Step 4: Run tests**

Run: `cargo test -p pgso-actuator-mcp`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add crates/pgso-actuator-mcp/
git commit -m "feat(actuator-mcp): implement McpActuator with MCP tools/list payload generation"
```

---

### Task 3: Mock second Signal

**Files:** Create: `crates/pgso-core/tests/e2e_mcp.rs`

- [ ] **Step 1: Define MockLatencySignal**

```rust
use std::collections::{HashSet, VecDeque};
use pgso_core::*;

/// A mock Signal simulating response-latency as a governance signal.
/// Proves the Signal trait admits more than prosody.
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
                axis: Axis::Arousal, // latency maps to arousal-like dimension
                confidence: 0.9,
                timestamp_ms: window.timestamp_ms,
            }]
        } else {
            vec![]
        }
    }
}
```

---

### Task 4: E2E over MCP

- [ ] **Step 1: Write E2E test with MCP actuator**

```rust
// In crates/pgso-core/tests/e2e_mcp.rs

fn dummy_window(ts: u64) -> AudioWindow {
    AudioWindow { samples: vec![0.0; 12800], sample_rate: 16000, timestamp_ms: ts }
}

fn reading(value: f32, axis: Axis, confidence: f32, ts: u64) -> SignalReading {
    SignalReading { value, axis, confidence, timestamp_ms: ts }
}

struct MockSignal {
    sequence: VecDeque<Vec<SignalReading>>,
}
impl MockSignal {
    fn new(readings: Vec<Vec<SignalReading>>) -> Self {
        Self { sequence: VecDeque::from(readings) }
    }
}
impl Signal for MockSignal {
    fn extract(&mut self, _w: &AudioWindow) -> Vec<SignalReading> {
        self.sequence.pop_front().unwrap_or_default()
    }
}

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

    let catalog = Catalog::new(vec![
        Tool::new("search", "Web Search"),
        Tool::new("calculate", "Calculator"),
        Tool::new("close_sale", "Close Sale"),
        Tool::new("escalate", "Escalate to Human"),
    ]);

    let mut readings = Vec::new();
    for i in 0..5 { readings.push(vec![reading(0.5, Axis::Valence, 0.9, i)]); }
    for i in 5..8 { readings.push(vec![reading(0.95, Axis::Valence, 0.9, i)]); }

    let actuator = pgso_actuator_mcp::McpActuator::new(catalog, protected.clone());
    let config = EngineConfig {
        confidence_threshold: 0.5, deviation_threshold: 0.3,
        hysteresis_window: 3, ema_alpha: 0.1, warmup_readings: 5,
        population_prior: 0.5,
    };

    let mut pgso = Pgso::builder()
        .signal(MockSignal::new(readings))
        .actuator(actuator)
        .engine(DecisionEngine::new(config))
        .rules(RuleEngine::new(rules, protected))
        .build();

    for i in 0..8 { pgso.process_window(&dummy_window(i)).unwrap(); }

    let catalog = pgso.current_catalog();
    assert!(!catalog.contains(&ToolId::from("close_sale")));
    assert!(catalog.contains(&ToolId::from("escalate")), "G2 over MCP");
}

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

    // Calm readings then sustained high latency
    let mut latencies = Vec::new();
    for _ in 0..5 { latencies.push(0.5); }
    for _ in 5..8 { latencies.push(0.95); }

    let signal = MockLatencySignal::new(latencies);
    let actuator = pgso_actuator_local::LocalActuator::new(catalog, protected.clone());
    let config = EngineConfig {
        confidence_threshold: 0.5, deviation_threshold: 0.3,
        hysteresis_window: 3, ema_alpha: 0.1, warmup_readings: 5,
        population_prior: 0.5,
    };

    let mut pgso = Pgso::builder()
        .signal(signal)
        .actuator(actuator)
        .engine(DecisionEngine::new(config))
        .rules(RuleEngine::new(rules, protected))
        .build();

    for i in 0..8 { pgso.process_window(&dummy_window(i)).unwrap(); }

    // The mock latency signal should have triggered step-up
    let tool = pgso.current_catalog().find(&ToolId::from("close_sale")).unwrap();
    assert!(tool.requires_step_up, "latency signal should trigger step-up");
}
```

- [ ] **Step 2: Run tests**

Run: `cargo test --test e2e_mcp`
Expected: all tests PASS

---

### Task 5: Agnosticism proof (diff check)

- [ ] **Step 1: Verify pgso-core is untouched**

Run: `git diff HEAD -- crates/pgso-core/src/`
Expected: EMPTY diff (no changes to core source)

If the diff shows ANY changes to `pgso-core/src/`, the milestone has violated R6 — investigate and re-scope.

- [ ] **Step 2: Run full workspace tests**

Run: `cargo test --workspace`
Run: `cargo clippy --workspace -- -D warnings`
Expected: all green

- [ ] **Step 3: Commit**

```bash
git add crates/pgso-actuator-mcp/ crates/pgso-core/tests/e2e_mcp.rs
git commit -m "feat(actuator-mcp): M5 complete — transport agnosticism demonstrated, not claimed"
```

---

## Definition of done

- [x] `pgso-actuator-mcp` implements `Actuator`
- [x] M4 scenario runs identically over MCP by swapping adapter
- [x] Mock `MockLatencySignal` accepted with zero core changes
- [x] `git diff HEAD -- crates/pgso-core/src/` is EMPTY — the headline result
- [x] All guarantees hold over new transport
- [x] `cargo clippy -- -D warnings` clean

---

## STOP HERE

Do NOT:
- start sales/domain validation (Milestone 6)
- implement HTTP/Olive adapter
- migrate signal to eGeMAPS

When DONE: PGSO is agnostic to both transport (local, MCP) and signal source (prosody, latency). The same deterministic core governs different backends through narrow trait boundaries. That demonstration is a citable engineering contribution.
