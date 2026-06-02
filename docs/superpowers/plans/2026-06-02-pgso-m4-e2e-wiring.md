# Milestone 4 — End-to-End Wiring (the PoC)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect Signal → DecisionEngine → RuleEngine → Actuator into one pipeline. When sustained altered prosody is perceived, the catalog loses the targeted tool, deterministically and with an audit trail. This is the complete proof of concept.

**Architecture:** A generic `Pgso<S: Signal, A: Actuator>` builder in `pgso-core`. E2E tests use a `MockSignal` (pre-programmed `SignalReading` sequences) + `LocalActuator` to verify the full governance loop without model noise.

**Tech Stack:** Rust 2021, pgso-core, pgso-actuator-local

**Depends on:** Milestones 1, 2, 3 all DONE.

---

## File structure

```
crates/pgso-core/
└── src/
    ├── lib.rs                        # (MODIFY: add pipeline module)
    └── pipeline.rs                   # (CREATE) Pgso builder + process loop
crates/pgso-core/
└── tests/
    └── e2e.rs                        # (CREATE) end-to-end integration tests
```

---

## Requirements (EARS form)

**REQ-4.1** THE `pgso-core` crate SHALL provide a `Pgso<S: Signal, A: Actuator>` builder that wires a `Signal`, `RuleEngine`, `DecisionEngine`, and `Actuator` into one pipeline.

**REQ-4.2** WHEN audio is streamed through the pipeline AND deviation is sustained above threshold with sufficient confidence, THE pipeline SHALL apply rule-defined actions to the catalog.

**REQ-4.3** WHEN a prunable tool is targeted, THE resulting catalog SHALL NOT contain that tool.

**REQ-4.4** WHEN deviation returns to baseline past hysteresis, THE pipeline SHALL restore the catalog to nominal AND remove directive blocks (G1).

**REQ-4.5 (G2 E2E)** WHEN a rule targets a protected tool, THE catalog SHALL still contain that tool through the full pipeline.

**REQ-4.6 (G5 E2E)** EVERY catalog mutation SHALL be recorded in the `AuditLog`.

**REQ-4.7 (determinism E2E)** GIVEN an identical `SignalReading` trace replayed twice, THE sequence of catalog states SHALL be identical.

---

## Tasks

### Task 1: MockSignal for testing

**Files:** Add to `crates/pgso-core/tests/e2e.rs`

- [ ] **Step 1: Define MockSignal**

```rust
use std::collections::{HashSet, VecDeque};
use pgso_core::*;

/// A mock Signal that replays pre-programmed readings.
struct MockSignal {
    sequence: VecDeque<Vec<SignalReading>>,
}

impl MockSignal {
    fn new(readings: Vec<Vec<SignalReading>>) -> Self {
        Self { sequence: VecDeque::from(readings) }
    }
}

impl Signal for MockSignal {
    fn extract(&mut self, _window: &AudioWindow) -> Vec<SignalReading> {
        self.sequence.pop_front().unwrap_or_default()
    }
}

fn dummy_window(ts: u64) -> AudioWindow {
    AudioWindow { samples: vec![0.0; 12800], sample_rate: 16000, timestamp_ms: ts }
}

fn reading(value: f32, axis: Axis, confidence: f32, ts: u64) -> SignalReading {
    SignalReading { value, axis, confidence, timestamp_ms: ts }
}

fn test_catalog() -> Catalog {
    Catalog::new(vec![
        Tool::new("search", "Web Search"),
        Tool::new("calculate", "Calculator"),
        Tool::new("close_sale", "Close Sale"),
        Tool::new("escalate", "Escalate to Human"),
    ])
}
```

---

### Task 2: Pgso builder and pipeline

**Files:** Create: `crates/pgso-core/src/pipeline.rs`

- [ ] **Step 1: Write failing E2E tests**

```rust
// In crates/pgso-core/tests/e2e.rs

#[test]
fn test_e2e_sustained_deviation_prunes_tool() {
    let protected = HashSet::from([ToolId::from("escalate")]);
    let rules = pgso_rules! {
        rule "high_valence_prune" {
            axis: Valence,
            deviation: 0.3,
            confidence: 0.5,
            action: Action::Prune(ToolId::from("close_sale"))
        }
    };

    // 5 calm readings (warm up) + 3 sustained high readings
    let mut readings = Vec::new();
    for i in 0..5 {
        readings.push(vec![reading(0.5, Axis::Valence, 0.9, i)]);
    }
    for i in 5..8 {
        readings.push(vec![reading(0.95, Axis::Valence, 0.9, i)]);
    }

    let signal = MockSignal::new(readings);
    let actuator = pgso_actuator_local::LocalActuator::new(test_catalog(), protected.clone());

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

    // Process all windows
    for i in 0..8 {
        pgso.process_window(&dummy_window(i)).unwrap();
    }

    let catalog = pgso.current_catalog();
    assert!(!catalog.contains(&ToolId::from("close_sale")), "close_sale should be pruned");
    assert!(catalog.contains(&ToolId::from("escalate")), "escalate must survive (G2)");
}

#[test]
fn test_e2e_recovery_restores_catalog() {
    let protected = HashSet::from([ToolId::from("escalate")]);
    let rules = pgso_rules! {
        rule "high_valence_prune" {
            axis: Valence,
            deviation: 0.3,
            confidence: 0.5,
            action: Action::Prune(ToolId::from("close_sale"))
        }
    };

    // Warm up + sustained high + recovery to calm
    let mut readings = Vec::new();
    for i in 0..5 { readings.push(vec![reading(0.5, Axis::Valence, 0.9, i)]); }
    for i in 5..8 { readings.push(vec![reading(0.95, Axis::Valence, 0.9, i)]); }
    for i in 8..12 { readings.push(vec![reading(0.5, Axis::Valence, 0.9, i)]); }

    let signal = MockSignal::new(readings);
    let actuator = pgso_actuator_local::LocalActuator::new(test_catalog(), protected.clone());
    let config = EngineConfig {
        confidence_threshold: 0.5, deviation_threshold: 0.3,
        hysteresis_window: 3, ema_alpha: 0.1, warmup_readings: 5,
        population_prior: 0.5,
    };

    let mut pgso = Pgso::builder()
        .signal(signal).actuator(actuator)
        .engine(DecisionEngine::new(config))
        .rules(RuleEngine::new(rules, protected))
        .build();

    for i in 0..12 { pgso.process_window(&dummy_window(i)).unwrap(); }

    let catalog = pgso.current_catalog();
    assert_eq!(catalog.len(), 4, "catalog should be fully restored after recovery");
    assert!(catalog.contains(&ToolId::from("close_sale")));
}

#[test]
fn test_e2e_protected_tool_survives() {
    let protected = HashSet::from([ToolId::from("escalate")]);
    let rules = pgso_rules! {
        rule "prune_escalate" {
            axis: Valence,
            deviation: 0.3,
            confidence: 0.5,
            action: Action::Prune(ToolId::from("escalate"))
        }
    };

    let mut readings = Vec::new();
    for i in 0..5 { readings.push(vec![reading(0.5, Axis::Valence, 0.9, i)]); }
    for i in 5..8 { readings.push(vec![reading(0.95, Axis::Valence, 0.9, i)]); }

    let signal = MockSignal::new(readings);
    let actuator = pgso_actuator_local::LocalActuator::new(test_catalog(), protected.clone());
    let config = EngineConfig {
        confidence_threshold: 0.5, deviation_threshold: 0.3,
        hysteresis_window: 3, ema_alpha: 0.1, warmup_readings: 5,
        population_prior: 0.5,
    };

    let mut pgso = Pgso::builder()
        .signal(signal).actuator(actuator)
        .engine(DecisionEngine::new(config))
        .rules(RuleEngine::new(rules, protected))
        .build();

    for i in 0..8 { pgso.process_window(&dummy_window(i)).unwrap(); }

    assert!(pgso.current_catalog().contains(&ToolId::from("escalate")),
        "G2: protected tool must ALWAYS survive");
}

#[test]
fn test_e2e_audit_trail() {
    let protected = HashSet::from([ToolId::from("escalate")]);
    let rules = pgso_rules! {
        rule "step_up" {
            axis: Valence,
            deviation: 0.3,
            confidence: 0.5,
            action: Action::RequireStepUp(ToolId::from("close_sale"))
        }
    };

    let mut readings = Vec::new();
    for i in 0..5 { readings.push(vec![reading(0.5, Axis::Valence, 0.9, i)]); }
    for i in 5..8 { readings.push(vec![reading(0.95, Axis::Valence, 0.9, i)]); }

    let signal = MockSignal::new(readings);
    let actuator = pgso_actuator_local::LocalActuator::new(test_catalog(), protected.clone());
    let config = EngineConfig {
        confidence_threshold: 0.5, deviation_threshold: 0.3,
        hysteresis_window: 3, ema_alpha: 0.1, warmup_readings: 5,
        population_prior: 0.5,
    };

    let mut pgso = Pgso::builder()
        .signal(signal).actuator(actuator)
        .engine(DecisionEngine::new(config))
        .rules(RuleEngine::new(rules, protected))
        .build();

    for i in 0..8 { pgso.process_window(&dummy_window(i)).unwrap(); }

    let entries = pgso.audit_log().entries();
    assert!(!entries.is_empty(), "audit log should have entries");
    // At least one entry should have a populated audit record
    let last = entries.last().unwrap();
    assert!(last.audit.rule_id.is_some());
    assert!(last.audit.deviation.is_some());
}

#[test]
fn test_e2e_determinism_on_trace() {
    let protected = HashSet::from([ToolId::from("escalate")]);
    let rules_fn = || pgso_rules! {
        rule "prune" {
            axis: Valence,
            deviation: 0.3,
            confidence: 0.5,
            action: Action::Prune(ToolId::from("close_sale"))
        }
    };

    let make_readings = || {
        let mut r = Vec::new();
        for i in 0..5 { r.push(vec![reading(0.5, Axis::Valence, 0.9, i)]); }
        for i in 5..8 { r.push(vec![reading(0.95, Axis::Valence, 0.9, i)]); }
        r
    };

    let config = EngineConfig {
        confidence_threshold: 0.5, deviation_threshold: 0.3,
        hysteresis_window: 3, ema_alpha: 0.1, warmup_readings: 5,
        population_prior: 0.5,
    };

    let mut pgso1 = Pgso::builder()
        .signal(MockSignal::new(make_readings()))
        .actuator(pgso_actuator_local::LocalActuator::new(test_catalog(), protected.clone()))
        .engine(DecisionEngine::new(config.clone()))
        .rules(RuleEngine::new(rules_fn(), protected.clone()))
        .build();

    let mut pgso2 = Pgso::builder()
        .signal(MockSignal::new(make_readings()))
        .actuator(pgso_actuator_local::LocalActuator::new(test_catalog(), protected.clone()))
        .engine(DecisionEngine::new(config))
        .rules(RuleEngine::new(rules_fn(), protected))
        .build();

    for i in 0..8 {
        let c1 = pgso1.process_window(&dummy_window(i)).unwrap();
        let c2 = pgso2.process_window(&dummy_window(i)).unwrap();
        assert_eq!(c1, c2, "determinism violated at window {}", i);
    }
}
```

- [ ] **Step 2: Implement Pgso builder**

```rust
// crates/pgso-core/src/pipeline.rs

use crate::{
    action::{Action, ScopeDecision},
    audit::AuditLog,
    engine::DecisionEngine,
    error::ActuatorError,
    rules::RuleEngine,
    traits::{Actuator, Signal},
    types::{AudioWindow, Catalog},
};

pub struct Pgso<S: Signal, A: Actuator> {
    signal: S,
    engine: DecisionEngine,
    rules: RuleEngine,
    actuator: A,
    audit_log: AuditLog,
}

impl<S: Signal, A: Actuator> Pgso<S, A> {
    pub fn builder() -> PgsoBuilder<S, A> {
        PgsoBuilder::default()
    }

    /// Process one audio window through the full pipeline.
    /// Returns the resulting catalog after any governance actions.
    pub fn process_window(&mut self, window: &AudioWindow) -> Result<Catalog, ActuatorError> {
        let readings = self.signal.extract(window);

        let mut any_triggered = false;
        for reading in &readings {
            if let Some(output) = self.engine.process(reading) {
                any_triggered = true;
                let decisions = self.rules.evaluate(&output);
                for decision in &decisions {
                    self.actuator.apply(decision)?;
                    self.audit_log.record(decision);
                }
            }
        }

        // If no trigger on this window and engine was previously triggered,
        // the engine returning None means deviation subsided → restore catalog
        if !any_triggered && !readings.is_empty() {
            let allow = ScopeDecision::new(Action::Allow);
            self.actuator.apply(&allow)?;
        }

        Ok(self.actuator.current_catalog())
    }

    pub fn current_catalog(&self) -> Catalog {
        self.actuator.current_catalog()
    }

    pub fn audit_log(&self) -> &AuditLog {
        &self.audit_log
    }
}

pub struct PgsoBuilder<S: Signal, A: Actuator> {
    signal: Option<S>,
    engine: Option<DecisionEngine>,
    rules: Option<RuleEngine>,
    actuator: Option<A>,
}

impl<S: Signal, A: Actuator> Default for PgsoBuilder<S, A> {
    fn default() -> Self {
        Self { signal: None, engine: None, rules: None, actuator: None }
    }
}

impl<S: Signal, A: Actuator> PgsoBuilder<S, A> {
    pub fn signal(mut self, signal: S) -> Self { self.signal = Some(signal); self }
    pub fn engine(mut self, engine: DecisionEngine) -> Self { self.engine = Some(engine); self }
    pub fn rules(mut self, rules: RuleEngine) -> Self { self.rules = Some(rules); self }
    pub fn actuator(mut self, actuator: A) -> Self { self.actuator = Some(actuator); self }

    pub fn build(self) -> Pgso<S, A> {
        Pgso {
            signal: self.signal.expect("signal required"),
            engine: self.engine.expect("engine required"),
            rules: self.rules.expect("rules required"),
            actuator: self.actuator.expect("actuator required"),
            audit_log: AuditLog::new(),
        }
    }
}
```

**Note:** `build()` uses `expect` because a missing component is a programmer error, not a runtime condition. In a library, consider returning `Result` with a custom error. For the PoC, this is acceptable.

- [ ] **Step 3: Update lib.rs**

Add to `crates/pgso-core/src/lib.rs`:
```rust
pub mod pipeline;
pub use pipeline::Pgso;
```

- [ ] **Step 4: Run E2E tests**

Run: `cargo test -p pgso-core --test e2e`
Expected: all 5 E2E tests PASS

- [ ] **Step 5: Run all tests + clippy**

Run: `cargo test --workspace`
Run: `cargo clippy --workspace -- -D warnings`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add crates/pgso-core/src/pipeline.rs crates/pgso-core/tests/e2e.rs
git commit -m "feat(core): Pgso pipeline — end-to-end governance PoC with all guarantees verified"
```

---

## Definition of done

- [x] `Pgso` builder wires all stages; pipeline runs against `LocalActuator`
- [x] Sustained altered prosody → tool removed → calm → tool restored, logged
- [x] All 5 E2E tests pass
- [x] Determinism test passes (mock trace, not live model)
- [x] INV-1..5 and G1..5 hold end-to-end
- [x] `cargo clippy -- -D warnings` clean

---

## STOP HERE

Do NOT:
- add MCP or HTTP adapter (Milestone 5)
- begin domain validation (Milestone 6)
- optimize the signal model

When DONE: a paralinguistic signal deterministically governs an agent's tool catalog, with safety guarantees verified. That is the thesis artifact in embryo.
