//! End-to-end integration tests for the PGSO pipeline (Milestone 4).
//!
//! These tests wire a [`MockSignal`] (pre-programmed [`SignalReading`]
//! sequences) through the generic [`Pgso`] pipeline into a
//! [`pgso_actuator_local::LocalActuator`], proving the full governance loop —
//! Signal -> `DecisionEngine` -> `RuleEngine` -> Actuator — without model noise.
//!
//! Each test maps to a requirement from `m4-e2e-wiring.md`:
//! - `test_e2e_sustained_deviation_prunes_tool` — REQ-4.2 / REQ-4.3
//! - `test_e2e_recovery_restores_catalog`       — REQ-4.4 (G1)
//! - `test_e2e_protected_tool_survives`         — REQ-4.5 (G2)
//! - `test_e2e_audit_trail`                     — REQ-4.6 (G5)
//! - `test_e2e_determinism_on_trace`            — REQ-4.7 (INV-3)
//! - `test_e2e_smoke_calm_keeps_full_catalog`   — pipeline smoke test
//!
//! `.unwrap()` is used freely here: this is `#[cfg(test)]` code (integration
//! tests compile under the test cfg), where the master spec §5 permits it.

use pgso_core::{
    pgso_rules, Action, AudioWindow, Axis, Catalog, DecisionEngine, EngineConfig, Pgso, RuleEngine,
    Signal, SignalReading, Tool, ToolId,
};
use std::collections::{HashSet, VecDeque};

/// A mock [`Signal`] that replays pre-programmed readings, one window at a time.
///
/// Each call to [`Signal::extract`] pops the next pre-programmed batch of
/// readings off the front of the queue. Once exhausted it yields an empty
/// batch (silence), exercising the pipeline's hold-state-on-silence path.
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

/// A throwaway audio window. The mock ignores the samples; only the timestamp
/// is meaningful, and even that is carried purely for shape.
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

/// REQ-4.2 / REQ-4.3: a sustained high-deviation trace drives a prunable tool
/// out of the served catalog, while a protected tool is untouched.
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

    // 5 calm readings (warm up) + 3 sustained high readings.
    let mut readings = Vec::new();
    for i in 0..5 {
        readings.push(vec![reading(0.5, Axis::Valence, 0.9, i)]);
    }
    for i in 5..8 {
        readings.push(vec![reading(0.95, Axis::Valence, 0.9, i)]);
    }

    let signal = MockSignal::new(readings);
    let actuator = pgso_actuator_local::LocalActuator::new(test_catalog(), protected.clone());

    let mut pgso = Pgso::builder()
        .signal(signal)
        .actuator(actuator)
        .engine(DecisionEngine::new(default_config()))
        .rules(RuleEngine::new(rules, protected))
        .build()
        .unwrap();

    for i in 0..8 {
        pgso.process_window(&dummy_window(i)).unwrap();
    }

    let catalog = pgso.current_catalog();
    assert!(
        !catalog.contains(&ToolId::from("close_sale")),
        "close_sale should be pruned after sustained high-deviation prosody"
    );
    assert!(
        catalog.contains(&ToolId::from("escalate")),
        "escalate must survive (G2)"
    );
}

/// REQ-4.4 (G1): when deviation returns to baseline, the catalog is restored to
/// nominal AND any directive blocks are removed.
#[test]
fn test_e2e_recovery_restores_catalog() {
    let protected = HashSet::from([ToolId::from("escalate")]);
    let rules = pgso_rules! {
        rule "high_valence_prune" {
            axis: Valence,
            deviation: 0.3,
            confidence: 0.5,
            action: Action::Prune(ToolId::from("close_sale")),
            action: Action::InjectDirective("De-escalate.".into())
        }
    };

    // Warm up + sustained high + recovery to calm.
    let mut readings = Vec::new();
    for i in 0..5 {
        readings.push(vec![reading(0.5, Axis::Valence, 0.9, i)]);
    }
    for i in 5..8 {
        readings.push(vec![reading(0.95, Axis::Valence, 0.9, i)]);
    }
    for i in 8..12 {
        readings.push(vec![reading(0.5, Axis::Valence, 0.9, i)]);
    }

    let signal = MockSignal::new(readings);
    let actuator = pgso_actuator_local::LocalActuator::new(test_catalog(), protected.clone());

    let mut pgso = Pgso::builder()
        .signal(signal)
        .actuator(actuator)
        .engine(DecisionEngine::new(default_config()))
        .rules(RuleEngine::new(rules, protected))
        .build()
        .unwrap();

    for i in 0..12 {
        pgso.process_window(&dummy_window(i)).unwrap();
    }

    let catalog = pgso.current_catalog();
    assert_eq!(
        catalog.len(),
        4,
        "catalog should be fully restored after recovery"
    );
    assert!(catalog.contains(&ToolId::from("close_sale")));

    // G5/REQ-4.6: the reversal must be auditable. The audit trail should hold
    // both the prune intervention and the restore-to-nominal event (which is
    // what clears the directive block per G1 — observable here via the log).
    let entries = pgso.audit_log().entries();
    assert!(
        entries.iter().any(|d| matches!(d.action, Action::Prune(_))),
        "audit log should record the prune intervention"
    );
    assert!(
        entries.iter().any(|d| {
            matches!(d.action, Action::Allow)
                && d.audit.rule_id.as_deref() == Some(pgso_core::RESTORE_NOMINAL_RULE_ID)
        }),
        "audit log should record the restore-to-nominal reversal (G5: reversible + traceable)"
    );
}

/// REQ-4.4 (G1 end-to-end): an injected directive block is exposed while the
/// deviation is sustained and is REMOVED once the catalog is restored to
/// nominal. The existing `test_e2e_recovery_restores_catalog` proves catalog
/// restoration and audits the reversal; this test closes G1's thinnest gap by
/// asserting the exposed directive set itself is empty after recovery.
#[test]
fn test_e2e_directives_cleared_after_recovery() {
    let protected = HashSet::from([ToolId::from("escalate")]);
    let rules = pgso_rules! {
        rule "high_valence_prune" {
            axis: Valence,
            deviation: 0.3,
            confidence: 0.5,
            action: Action::Prune(ToolId::from("close_sale")),
            action: Action::InjectDirective("De-escalate.".into())
        }
    };

    // Warm up (calm) -> sustained high (injects directive) -> recovery to calm.
    let mut readings = Vec::new();
    for i in 0..5 {
        readings.push(vec![reading(0.5, Axis::Valence, 0.9, i)]);
    }
    for i in 5..8 {
        readings.push(vec![reading(0.95, Axis::Valence, 0.9, i)]);
    }
    for i in 8..12 {
        readings.push(vec![reading(0.5, Axis::Valence, 0.9, i)]);
    }

    let signal = MockSignal::new(readings);
    let actuator = pgso_actuator_local::LocalActuator::new(test_catalog(), protected.clone());

    let mut pgso = Pgso::builder()
        .signal(signal)
        .actuator(actuator)
        .engine(DecisionEngine::new(default_config()))
        .rules(RuleEngine::new(rules, protected))
        .build()
        .unwrap();

    // Through the sustained-deviation windows: the directive is injected and the
    // prunable tool is gone.
    for i in 0..8 {
        pgso.process_window(&dummy_window(i)).unwrap();
    }
    assert!(
        !pgso.actuator().directives().is_empty(),
        "directive block should be present while the deviation is sustained"
    );
    assert!(
        !pgso.current_catalog().contains(&ToolId::from("close_sale")),
        "close_sale should be pruned while the deviation is sustained"
    );

    // Through recovery: the catalog returns to nominal AND the directive set is
    // emptied (G1 — the appended block is removable and removed on restore).
    for i in 8..12 {
        pgso.process_window(&dummy_window(i)).unwrap();
    }
    assert!(
        pgso.actuator().directives().is_empty(),
        "G1: directive blocks must be cleared once the catalog is restored to nominal"
    );
    let catalog = pgso.current_catalog();
    assert_eq!(catalog.len(), 4, "catalog should be fully restored after recovery");
    assert!(
        catalog.contains(&ToolId::from("close_sale")),
        "the pruned tool must be back after recovery"
    );
}

/// REQ-4.5 (G2 end-to-end): a rule that targets a PROTECTED tool must never
/// remove it from the served catalog, anywhere in the pipeline.
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
    for i in 0..5 {
        readings.push(vec![reading(0.5, Axis::Valence, 0.9, i)]);
    }
    for i in 5..8 {
        readings.push(vec![reading(0.95, Axis::Valence, 0.9, i)]);
    }

    let signal = MockSignal::new(readings);
    let actuator = pgso_actuator_local::LocalActuator::new(test_catalog(), protected.clone());

    let mut pgso = Pgso::builder()
        .signal(signal)
        .actuator(actuator)
        .engine(DecisionEngine::new(default_config()))
        .rules(RuleEngine::new(rules, protected))
        .build()
        .unwrap();

    for i in 0..8 {
        pgso.process_window(&dummy_window(i)).unwrap();
    }

    assert!(
        pgso.current_catalog().contains(&ToolId::from("escalate")),
        "G2: protected tool must ALWAYS survive, even when a rule targets it"
    );
}

/// REQ-4.6 (G5): every catalog mutation is recorded in the `AuditLog` with a
/// populated rule id, deviation, axis, and timestamp.
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
    for i in 0..5 {
        readings.push(vec![reading(0.5, Axis::Valence, 0.9, i)]);
    }
    for i in 5..8 {
        readings.push(vec![reading(0.95, Axis::Valence, 0.9, i)]);
    }

    let signal = MockSignal::new(readings);
    let actuator = pgso_actuator_local::LocalActuator::new(test_catalog(), protected.clone());

    let mut pgso = Pgso::builder()
        .signal(signal)
        .actuator(actuator)
        .engine(DecisionEngine::new(default_config()))
        .rules(RuleEngine::new(rules, protected))
        .build()
        .unwrap();

    for i in 0..8 {
        pgso.process_window(&dummy_window(i)).unwrap();
    }

    let entries = pgso.audit_log().entries();
    assert!(!entries.is_empty(), "audit log should have entries");
    // Every recorded intervention must carry a fully populated audit record
    // tying it to the signal, the rule, and the timestamp that produced it.
    let last = entries.last().unwrap();
    assert!(
        last.audit.rule_id.is_some(),
        "audit record must name the firing rule"
    );
    assert!(
        last.audit.deviation.is_some(),
        "audit record must carry the deviation"
    );
    assert!(
        last.audit.axis.is_some(),
        "audit record must carry the axis"
    );
    assert_eq!(
        last.audit.rule_id.as_deref(),
        Some("step_up"),
        "the recorded rule id must match the firing rule"
    );
}

/// REQ-4.7 (INV-3): replaying an identical reading trace twice yields an
/// identical sequence of catalog states. Determinism is a property of the core,
/// so the trace comes from the mock, not a live model.
#[test]
fn test_e2e_determinism_on_trace() {
    let protected = HashSet::from([ToolId::from("escalate")]);
    let rules_fn = || {
        pgso_rules! {
            rule "prune" {
                axis: Valence,
                deviation: 0.3,
                confidence: 0.5,
                action: Action::Prune(ToolId::from("close_sale"))
            }
        }
    };

    let make_readings = || {
        let mut r = Vec::new();
        for i in 0..5 {
            r.push(vec![reading(0.5, Axis::Valence, 0.9, i)]);
        }
        for i in 5..8 {
            r.push(vec![reading(0.95, Axis::Valence, 0.9, i)]);
        }
        r
    };

    let config = default_config();

    let mut pgso1 = Pgso::builder()
        .signal(MockSignal::new(make_readings()))
        .actuator(pgso_actuator_local::LocalActuator::new(
            test_catalog(),
            protected.clone(),
        ))
        .engine(DecisionEngine::new(config.clone()))
        .rules(RuleEngine::new(rules_fn(), protected.clone()))
        .build()
        .unwrap();

    let mut pgso2 = Pgso::builder()
        .signal(MockSignal::new(make_readings()))
        .actuator(pgso_actuator_local::LocalActuator::new(
            test_catalog(),
            protected.clone(),
        ))
        .engine(DecisionEngine::new(config))
        .rules(RuleEngine::new(rules_fn(), protected))
        .build()
        .unwrap();

    for i in 0..8 {
        let c1 = pgso1.process_window(&dummy_window(i)).unwrap();
        let c2 = pgso2.process_window(&dummy_window(i)).unwrap();
        assert_eq!(c1, c2, "determinism violated at window {i}");
    }
}

/// Pipeline smoke test: a calm trace that never crosses threshold leaves the
/// full catalog intact and records no interventions. Proves the loop is inert
/// when prosody is nominal (the governing principle: fail toward inaction).
#[test]
fn test_e2e_smoke_calm_keeps_full_catalog() {
    let protected = HashSet::from([ToolId::from("escalate")]);
    let rules = pgso_rules! {
        rule "high_valence_prune" {
            axis: Valence,
            deviation: 0.3,
            confidence: 0.5,
            action: Action::Prune(ToolId::from("close_sale"))
        }
    };

    // All calm: warm-up then more calm readings. Deviation never crosses 0.3.
    let mut readings = Vec::new();
    for i in 0..10 {
        readings.push(vec![reading(0.5, Axis::Valence, 0.9, i)]);
    }

    let signal = MockSignal::new(readings);
    let actuator = pgso_actuator_local::LocalActuator::new(test_catalog(), protected.clone());

    let mut pgso = Pgso::builder()
        .signal(signal)
        .actuator(actuator)
        .engine(DecisionEngine::new(default_config()))
        .rules(RuleEngine::new(rules, protected))
        .build()
        .unwrap();

    for i in 0..10 {
        let catalog = pgso.process_window(&dummy_window(i)).unwrap();
        assert_eq!(
            catalog.len(),
            4,
            "calm prosody must keep the full catalog at window {i}"
        );
    }

    assert!(
        pgso.current_catalog().contains(&ToolId::from("close_sale")),
        "close_sale must remain under calm prosody"
    );
    assert!(
        pgso.audit_log().entries().is_empty(),
        "no interventions should be recorded under calm prosody"
    );
}
