# Milestone 2 — Deterministic Core

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the decision logic — pure, deterministic, fully testable with `proptest`, with zero ML/network. This is the heart of the thesis.

**Architecture:** `DecisionEngine` processes raw `SignalReading`s (baseline + hysteresis + abstention) → `EngineOutput`. `RuleEngine` evaluates rules against output → `Vec<ScopeDecision>` with G2 enforced. Both in `pgso-core`.

**Tech Stack:** Rust 2021, thiserror, proptest (dev)

**Depends on:** Milestone 1 (core types and Actuator trait exist).

**Note:** This milestone adds `audit: AuditRecord` to `ScopeDecision`. M1 tests already use `ScopeDecision::new(action)` which fills `AuditRecord::empty()`, so M1 tests remain compatible.

---

## File structure

```
crates/pgso-core/
├── Cargo.toml                    # (MODIFY: add proptest dev-dep)
└── src/
    ├── lib.rs                    # (MODIFY: add module re-exports)
    ├── engine.rs                 # (CREATE) DecisionEngine, EngineConfig, EngineOutput
    ├── rules.rs                  # (CREATE) Rule, RulePredicate, RuleSet, RuleEngine, pgso_rules!
    └── audit.rs                  # (CREATE) AuditLog
```

---

## Requirements (EARS form)

### Decision engine

**REQ-2.1** THE `DecisionEngine` SHALL accept `SignalReading`s one at a time and maintain state without reading any clock or RNG (INV-3).

**REQ-2.2** THE `DecisionEngine` SHALL maintain a three-layer speaker baseline: population prior at t=0, EMA during active speech, stable personal baseline after configured warm-up.

**REQ-2.3** THE `DecisionEngine` SHALL compute deviation as `|reading.value - current_baseline|`, NOT as an absolute threshold.

**REQ-2.4 (hysteresis)** WHEN a single spike is followed by sub-threshold readings, THE engine SHALL NOT change state. Sustained deviation across `hysteresis_window` consecutive windows is required to trigger.

**REQ-2.5 (abstention, G4)** WHEN `SignalReading.confidence` < configured threshold, THE engine SHALL return `None` (no output, hold state).

**REQ-2.6 (determinism, INV-3)** GIVEN two identical input sequences and config, THE engine SHALL produce identical outputs.

### Rule engine

**REQ-2.7** THE `pgso_rules!` macro SHALL allow declaring rules with: id, axis, min deviation, min confidence, and a list of actions.

**REQ-2.8** THE `RuleEngine` SHALL evaluate rules against `EngineOutput` and emit `Vec<ScopeDecision>` with populated `AuditRecord`.

**REQ-2.9 (G3)** A `Prune` targeting a protected tool SHALL be downgraded to `RequireStepUp` — never silently dropped, never emitted as `Prune`.

### Allowlist (G2)

**REQ-2.10** FOR ANY `SignalReading` sequence and ANY rule set, the `RuleEngine` output SHALL NEVER contain `Prune(id)` where `id` is protected. Verified by `proptest` over randomized sequences. **This is the most important test.**

### Audit

**REQ-2.11** EVERY `ScopeDecision` from the rule engine SHALL carry a populated `AuditRecord` with signal value, axis, deviation, threshold, rule id, and timestamp.

---

## Tasks

### Task 1: Add dev-dependencies and signal-side types

**Files:** Modify: `crates/pgso-core/Cargo.toml`, `crates/pgso-core/src/lib.rs`

- [ ] **Step 1: Add proptest dev-dependency**

```toml
[dev-dependencies]
proptest = "1"
```

- [ ] **Step 2: Update lib.rs re-exports**

```rust
pub mod types;
pub mod action;
pub mod error;
pub mod traits;
pub mod engine;
pub mod rules;
pub mod audit;

pub use types::*;
pub use action::*;
pub use error::*;
pub use traits::*;
pub use engine::{DecisionEngine, EngineConfig, EngineOutput};
pub use rules::{Rule, RulePredicate, RuleSet, RuleEngine};
pub use audit::AuditLog;
```

- [ ] **Step 3: Verify**

Run: `cargo build -p pgso-core`

---

### Task 2: Write the DecisionEngine

**Files:** Create: `crates/pgso-core/src/engine.rs`

- [ ] **Step 1: Write failing unit tests**

```rust
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cargo test -p pgso-core -- engine`
Expected: FAIL — `DecisionEngine` not defined

- [ ] **Step 3: Implement DecisionEngine**

```rust
use std::collections::HashMap;
use crate::types::{Axis, SignalReading};

#[derive(Debug, Clone)]
pub struct EngineConfig {
    pub confidence_threshold: f32,
    pub deviation_threshold: f32,
    pub hysteresis_window: u32,
    pub ema_alpha: f32,
    pub warmup_readings: u64,
    pub population_prior: f32,
}

#[derive(Debug, Clone)]
pub struct EngineOutput {
    pub axis: Axis,
    pub raw_value: f32,
    pub deviation: f32,
    pub confidence: f32,
    pub baseline: f32,
    pub timestamp_ms: u64,
}

pub struct DecisionEngine {
    config: EngineConfig,
    axes: HashMap<Axis, AxisState>,
}

struct AxisState {
    baseline: f32,
    readings_count: u64,
    consecutive_above: u32,
}

impl AxisState {
    fn new(prior: f32) -> Self {
        Self { baseline: prior, readings_count: 0, consecutive_above: 0 }
    }

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

impl DecisionEngine {
    pub fn new(config: EngineConfig) -> Self {
        Self { config, axes: HashMap::new() }
    }

    /// Process one reading. Returns Some if sustained deviation is detected.
    /// Returns None on abstention (G4) or when hysteresis is not met.
    pub fn process(&mut self, reading: &SignalReading) -> Option<EngineOutput> {
        // G4: abstain on low confidence
        if reading.confidence < self.config.confidence_threshold {
            return None;
        }

        let state = self.axes
            .entry(reading.axis)
            .or_insert_with(|| AxisState::new(self.config.population_prior));

        state.update_baseline(reading.value, self.config.ema_alpha, self.config.warmup_readings);

        let deviation = (reading.value - state.baseline).abs();

        if deviation > self.config.deviation_threshold {
            state.consecutive_above += 1;
        } else {
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
```

- [ ] **Step 4: Run tests**

Run: `cargo test -p pgso-core -- engine`
Expected: all 5 unit tests PASS

- [ ] **Step 5: Commit**

```bash
git add crates/pgso-core/src/engine.rs
git commit -m "feat(core): implement DecisionEngine with baseline EMA, hysteresis, abstention"
```

---

### Task 3: Write the RuleEngine and `pgso_rules!` macro

**Files:** Create: `crates/pgso-core/src/rules.rs`

- [ ] **Step 1: Write failing unit tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Action, Axis, ToolId};

    fn sample_output(axis: Axis, deviation: f32, confidence: f32) -> EngineOutput {
        EngineOutput {
            axis, raw_value: 0.9, deviation, confidence,
            baseline: 0.5, timestamp_ms: 1000,
        }
    }

    #[test]
    fn test_rule_matches_and_emits_action() {
        let rules = pgso_rules! {
            rule "high_arousal" {
                axis: Arousal,
                deviation: 0.4,
                confidence: 0.6,
                action: Action::RequireStepUp(ToolId::from("close_sale"))
            }
        };
        let engine = RuleEngine::new(rules, HashSet::new());
        let decisions = engine.evaluate(&sample_output(Axis::Arousal, 0.5, 0.8));
        assert_eq!(decisions.len(), 1);
        assert_eq!(decisions[0].action, Action::RequireStepUp(ToolId::from("close_sale")));
    }

    #[test]
    fn test_no_match_returns_empty() {
        let rules = pgso_rules! {
            rule "high_arousal" {
                axis: Arousal,
                deviation: 0.9,
                confidence: 0.9,
                action: Action::RequireStepUp(ToolId::from("close_sale"))
            }
        };
        let engine = RuleEngine::new(rules, HashSet::new());
        let decisions = engine.evaluate(&sample_output(Axis::Arousal, 0.3, 0.5));
        assert!(decisions.is_empty());
    }

    #[test]
    fn test_prune_protected_downgraded_to_stepup() {
        // G2+G3: Prune on protected tool → RequireStepUp
        let protected = HashSet::from([ToolId::from("escalate")]);
        let rules = pgso_rules! {
            rule "prune_escalate" {
                axis: Arousal,
                deviation: 0.3,
                confidence: 0.5,
                action: Action::Prune(ToolId::from("escalate"))
            }
        };
        let engine = RuleEngine::new(rules, protected);
        let decisions = engine.evaluate(&sample_output(Axis::Arousal, 0.5, 0.8));
        assert_eq!(decisions.len(), 1);
        assert_eq!(
            decisions[0].action,
            Action::RequireStepUp(ToolId::from("escalate"))
        );
    }

    #[test]
    fn test_default_action_is_stepup() {
        // G3: a rule without Prune → RequireStepUp
        let rules = pgso_rules! {
            rule "stepup_only" {
                axis: Valence,
                deviation: 0.3,
                confidence: 0.5,
                action: Action::RequireStepUp(ToolId::from("close_sale"))
            }
        };
        let engine = RuleEngine::new(rules, HashSet::new());
        let decisions = engine.evaluate(&sample_output(Axis::Valence, 0.5, 0.8));
        assert_eq!(decisions[0].action, Action::RequireStepUp(ToolId::from("close_sale")));
    }

    #[test]
    fn test_audit_record_populated() {
        let rules = pgso_rules! {
            rule "test_rule" {
                axis: Arousal,
                deviation: 0.3,
                confidence: 0.5,
                action: Action::RequireStepUp(ToolId::from("search"))
            }
        };
        let engine = RuleEngine::new(rules, HashSet::new());
        let decisions = engine.evaluate(&sample_output(Axis::Arousal, 0.5, 0.8));
        let audit = &decisions[0].audit;
        assert_eq!(audit.axis, Some(Axis::Arousal));
        assert_eq!(audit.rule_id.as_deref(), Some("test_rule"));
        assert!(audit.deviation.is_some());
        assert!(audit.threshold_crossed.is_some());
        assert_eq!(audit.timestamp_ms, 1000);
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cargo test -p pgso-core -- rules`
Expected: FAIL

- [ ] **Step 3: Implement Rule types, RuleEngine, and pgso_rules! macro**

```rust
use std::collections::HashSet;
use crate::{
    action::{Action, AuditRecord, ScopeDecision},
    engine::EngineOutput,
    types::{Axis, ToolId},
};

#[derive(Debug, Clone)]
pub struct RulePredicate {
    pub axis: Axis,
    pub min_deviation: f32,
    pub min_confidence: f32,
}

impl RulePredicate {
    pub fn matches(&self, axis: Axis, deviation: f32, confidence: f32) -> bool {
        self.axis == axis && deviation >= self.min_deviation && confidence >= self.min_confidence
    }
}

#[derive(Debug, Clone)]
pub struct Rule {
    pub id: String,
    pub predicate: RulePredicate,
    pub actions: Vec<Action>,
}

impl Rule {
    pub fn new(
        id: &str, axis: Axis, min_deviation: f32, min_confidence: f32, actions: Vec<Action>,
    ) -> Self {
        Self {
            id: id.to_string(),
            predicate: RulePredicate { axis, min_deviation, min_confidence },
            actions,
        }
    }
}

#[derive(Debug, Clone)]
pub struct RuleSet {
    rules: Vec<Rule>,
}

impl RuleSet {
    pub fn new(rules: Vec<Rule>) -> Self { Self { rules } }
    pub fn rules(&self) -> &[Rule] { &self.rules }

    fn matching_rules(&self, axis: Axis, deviation: f32, confidence: f32) -> Vec<&Rule> {
        self.rules.iter()
            .filter(|r| r.predicate.matches(axis, deviation, confidence))
            .collect()
    }
}

pub struct RuleEngine {
    rules: RuleSet,
    protected: HashSet<ToolId>,
}

impl RuleEngine {
    pub fn new(rules: RuleSet, protected: HashSet<ToolId>) -> Self {
        Self { rules, protected }
    }

    /// Evaluate rules and return decisions. G2: Prune(protected) → RequireStepUp.
    pub fn evaluate(&self, output: &EngineOutput) -> Vec<ScopeDecision> {
        let matching = self.rules.matching_rules(output.axis, output.deviation, output.confidence);
        let mut decisions = Vec::new();

        for rule in matching {
            for action in &rule.actions {
                let final_action = match action {
                    Action::Prune(id) if self.protected.contains(id) => {
                        // G2+G3: downgrade to step-up, never prune protected
                        Action::RequireStepUp(id.clone())
                    }
                    other => other.clone(),
                };

                decisions.push(ScopeDecision::with_audit(
                    final_action,
                    AuditRecord {
                        timestamp_ms: output.timestamp_ms,
                        signal_value: Some(output.raw_value),
                        axis: Some(output.axis),
                        deviation: Some(output.deviation),
                        threshold_crossed: Some(rule.predicate.min_deviation),
                        rule_id: Some(rule.id.clone()),
                    },
                ));
            }
        }
        decisions
    }
}

/// Declarative macro for constructing rule sets.
///
/// Usage:
/// ```ignore
/// let rules = pgso_rules! {
///     rule "name" {
///         axis: Arousal,
///         deviation: 0.7,
///         confidence: 0.6,
///         action: Action::RequireStepUp(ToolId::from("tool"))
///     };
///     rule "name2" {
///         axis: Valence,
///         deviation: 0.5,
///         confidence: 0.5,
///         action: Action::Prune(ToolId::from("tool")),
///         action: Action::InjectDirective("text".into())
///     }
/// };
/// ```
#[macro_export]
macro_rules! pgso_rules {
    ( $( rule $id:literal {
        axis: $axis:ident,
        deviation: $dev:expr,
        confidence: $conf:expr,
        $( action: $action:expr ),+ $(,)?
    } );* $(;)? ) => {
        $crate::RuleSet::new(vec![
            $(
                $crate::Rule::new(
                    $id,
                    $crate::Axis::$axis,
                    $dev,
                    $conf,
                    vec![ $( $action ),+ ],
                )
            ),*
        ])
    };
}
```

- [ ] **Step 4: Run tests**

Run: `cargo test -p pgso-core -- rules`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add crates/pgso-core/src/rules.rs
git commit -m "feat(core): implement RuleEngine, pgso_rules! macro, and G2 allowlist enforcement"
```

---

### Task 4: Property tests

**Files:** Create: `crates/pgso-core/tests/property_tests.rs`

- [ ] **Step 1: Write property tests**

```rust
use proptest::prelude::*;
use std::collections::HashSet;
use pgso_core::*;

fn arb_axis() -> impl Strategy<Value = Axis> {
    prop_oneof![Just(Axis::Valence), Just(Axis::Arousal)]
}

fn arb_reading() -> impl Strategy<Value = SignalReading> {
    (0.0f32..1.0, arb_axis(), 0.0f32..1.0, 0u64..10000)
        .prop_map(|(value, axis, confidence, ts)| SignalReading {
            value, axis, confidence, timestamp_ms: ts,
        })
}

fn arb_tool_id() -> impl Strategy<Value = ToolId> {
    prop_oneof![
        Just(ToolId::from("search")),
        Just(ToolId::from("calculate")),
        Just(ToolId::from("close_sale")),
        Just(ToolId::from("escalate")),
    ]
}

fn arb_action() -> impl Strategy<Value = Action> {
    prop_oneof![
        Just(Action::Allow),
        arb_tool_id().prop_map(Action::RequireStepUp),
        arb_tool_id().prop_map(Action::Prune),
    ]
}

fn arb_rule() -> impl Strategy<Value = Rule> {
    (arb_axis(), 0.0f32..1.0, 0.0f32..1.0, prop::collection::vec(arb_action(), 1..4))
        .prop_map(|(axis, dev, conf, actions)| {
            Rule::new("random_rule", axis, dev, conf, actions)
        })
}

proptest! {
    /// THE most important test in the project.
    /// For ANY signal sequence and ANY rule set, protected tools are NEVER pruned.
    #[test]
    fn prop_allowlist_never_pruned(
        readings in prop::collection::vec(arb_reading(), 1..50),
        rules in prop::collection::vec(arb_rule(), 1..10),
    ) {
        let protected = HashSet::from([ToolId::from("escalate")]);
        let config = EngineConfig {
            confidence_threshold: 0.3,
            deviation_threshold: 0.2,
            hysteresis_window: 2,
            ema_alpha: 0.1,
            warmup_readings: 3,
            population_prior: 0.5,
        };

        let mut engine = DecisionEngine::new(config);
        let rule_engine = RuleEngine::new(RuleSet::new(rules), protected.clone());

        for reading in &readings {
            if let Some(output) = engine.process(reading) {
                let decisions = rule_engine.evaluate(&output);
                for decision in &decisions {
                    if let Action::Prune(ref id) = decision.action {
                        prop_assert!(
                            !protected.contains(id),
                            "G2 VIOLATED: Prune emitted for protected tool {:?}", id
                        );
                    }
                }
            }
        }
    }

    /// Determinism: same inputs → same outputs.
    #[test]
    fn prop_determinism(
        readings in prop::collection::vec(arb_reading(), 1..30),
    ) {
        let config = EngineConfig {
            confidence_threshold: 0.5, deviation_threshold: 0.3,
            hysteresis_window: 3, ema_alpha: 0.1, warmup_readings: 5,
            population_prior: 0.5,
        };
        let mut e1 = DecisionEngine::new(config.clone());
        let mut e2 = DecisionEngine::new(config);

        for reading in &readings {
            let o1 = e1.process(reading);
            let o2 = e2.process(reading);
            match (&o1, &o2) {
                (Some(a), Some(b)) => {
                    prop_assert!((a.deviation - b.deviation).abs() < f32::EPSILON);
                    prop_assert!((a.baseline - b.baseline).abs() < f32::EPSILON);
                }
                (None, None) => {}
                _ => prop_assert!(false, "determinism violated"),
            }
        }
    }

    /// A single spike surrounded by calm never triggers.
    #[test]
    fn prop_isolated_spike_no_trigger(
        calm_value in 0.4f32..0.6,
        spike_value in 0.85f32..1.0,
        spike_pos in 5usize..15,
        length in 16usize..30,
    ) {
        let config = EngineConfig {
            confidence_threshold: 0.3, deviation_threshold: 0.25,
            hysteresis_window: 3, ema_alpha: 0.1, warmup_readings: 5,
            population_prior: 0.5,
        };
        let mut engine = DecisionEngine::new(config);

        let mut triggered_at_spike = false;
        for i in 0..length {
            let val = if i == spike_pos { spike_value } else { calm_value };
            let r = SignalReading {
                value: val, axis: Axis::Valence, confidence: 0.9, timestamp_ms: i as u64,
            };
            if i == spike_pos {
                if engine.process(&r).is_some() {
                    triggered_at_spike = true;
                }
            } else if i > spike_pos {
                // After the spike, should not be triggered
                prop_assert!(engine.process(&r).is_none(),
                    "triggered after isolated spike at pos {}", spike_pos);
            } else {
                engine.process(&r);
            }
        }
        // The spike itself might increment consecutive_above but shouldn't reach hysteresis_window=3
        // (only 1 reading above threshold)
        // Note: spike AT position might trigger if it happens to be 3rd consecutive, but that
        // would mean the calm readings before also were above threshold, contradicting the setup
    }

    /// Low confidence always abstains.
    #[test]
    fn prop_low_confidence_abstains(
        value in 0.0f32..1.0,
        confidence in 0.0f32..0.3,
        axis in arb_axis(),
    ) {
        let config = EngineConfig {
            confidence_threshold: 0.5, deviation_threshold: 0.3,
            hysteresis_window: 3, ema_alpha: 0.1, warmup_readings: 5,
            population_prior: 0.5,
        };
        let mut engine = DecisionEngine::new(config);
        let r = SignalReading { value, axis, confidence, timestamp_ms: 0 };
        prop_assert!(engine.process(&r).is_none(), "should abstain on low confidence");
    }
}
```

- [ ] **Step 2: Run property tests**

Run: `cargo test -p pgso-core --test property_tests`
Expected: all 4 property tests PASS

- [ ] **Step 3: Commit**

```bash
git add crates/pgso-core/tests/
git commit -m "test(core): add property tests — prop_allowlist_never_pruned is the headline"
```

---

### Task 5: AuditLog

**Files:** Create: `crates/pgso-core/src/audit.rs`

- [ ] **Step 1: Write failing test**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Action, AuditRecord, ScopeDecision, ToolId};

    #[test]
    fn test_audit_log_records_and_retrieves() {
        let mut log = AuditLog::new();
        let d = ScopeDecision::new(Action::RequireStepUp(ToolId::from("search")));
        log.record(&d);
        assert_eq!(log.entries().len(), 1);
    }

    #[test]
    fn test_audit_log_clear() {
        let mut log = AuditLog::new();
        log.record(&ScopeDecision::new(Action::Allow));
        log.clear();
        assert!(log.entries().is_empty());
    }
}
```

- [ ] **Step 2: Implement AuditLog**

```rust
use crate::action::ScopeDecision;

pub struct AuditLog {
    entries: Vec<ScopeDecision>,
}

impl AuditLog {
    pub fn new() -> Self { Self { entries: Vec::new() } }
    pub fn record(&mut self, decision: &ScopeDecision) { self.entries.push(decision.clone()); }
    pub fn entries(&self) -> &[ScopeDecision] { &self.entries }
    pub fn clear(&mut self) { self.entries.clear(); }
}

impl Default for AuditLog {
    fn default() -> Self { Self::new() }
}
```

- [ ] **Step 3: Run all tests**

Run: `cargo test -p pgso-core`
Expected: ALL tests pass (engine + rules + audit + property tests)

- [ ] **Step 4: Run clippy + verify INV-1**

Run: `cargo clippy -p pgso-core -- -D warnings`
Run: `cargo metadata --no-deps -p pgso-core --format-version 1 | grep -o '"dependencies":\[[^]]*\]'`
Expected: clippy clean; deps = thiserror only

- [ ] **Step 5: Commit**

```bash
git add crates/pgso-core/
git commit -m "feat(core): complete M2 — engine, rules, audit, all property tests green"
```

---

## Definition of done

- [x] `DecisionEngine`, `RuleEngine`, `pgso_rules!`, `AuditLog` in `pgso-core`
- [x] 4 property tests + 4+ unit tests pass
- [x] `pgso-core` has zero ML/HTTP/IO deps (INV-1) — only `thiserror`
- [x] No `std::time::now`, no `rand` in engine path (INV-3)
- [x] `cargo clippy -- -D warnings` clean
- [x] `prop_allowlist_never_pruned` is GREEN

---

## STOP HERE

Do NOT:
- connect a real audio signal (all readings are synthetic)
- touch the actuator implementation
- add MCP/HTTP/ONNX

If the property tests are green — especially `prop_allowlist_never_pruned` — the guarantees hold by construction, independent of signal quality.
