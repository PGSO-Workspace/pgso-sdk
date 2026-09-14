//! Research audit contracts, 2026-09-14, against upstream 1b17c248d360.
//! Regression contracts for issues reproduced against the original snapshot.
use pgso_actuator_local::LocalActuator;
use pgso_actuator_mcp::McpActuator;
use pgso_core::{
    Action, Actuator, AudioWindow, Axis, Catalog, DecisionEngine, EngineConfig, Pgso, Rule,
    RuleEngine, RuleSet, ScopeDecision, Signal, SignalReading, Tool, ToolId,
};
use std::collections::{HashSet, VecDeque};

struct Scripted(VecDeque<Vec<SignalReading>>);
impl Signal for Scripted {
    fn extract(&mut self, _: &AudioWindow) -> Vec<SignalReading> {
        self.0.pop_front().unwrap_or_default()
    }
}
fn config() -> EngineConfig {
    EngineConfig {
        confidence_threshold: 0.5,
        deviation_threshold: 0.2,
        hysteresis_window: 1,
        ema_alpha: 0.0,
        warmup_readings: 0,
        population_prior: 0.5,
    }
}
fn reading(value: f32, confidence: f32, timestamp_ms: u64) -> SignalReading {
    SignalReading {
        value,
        axis: Axis::Arousal,
        confidence,
        timestamp_ms,
    }
}
fn catalog() -> Catalog {
    Catalog::new(vec![
        Tool::new("quote", "Quote"),
        Tool::new("human", "Human"),
    ])
}
fn rule(id: &str, threshold: f32, action: Action) -> Rule {
    Rule::new(id, Axis::Arousal, threshold, 0.5, vec![action])
}
fn prune() -> Rule {
    rule("restrict", 0.2, Action::Prune(ToolId::from("quote")))
}
fn directive() -> Rule {
    rule("clarify", 0.2, Action::InjectDirective("Clarify.".into()))
}
fn pipeline(windows: Vec<Vec<SignalReading>>, rules: Vec<Rule>) -> Pgso<Scripted, LocalActuator> {
    Pgso::builder()
        .signal(Scripted(windows.into()))
        .engine(DecisionEngine::new(config()))
        .rules(RuleEngine::new(RuleSet::new(rules), HashSet::new()))
        .actuator(LocalActuator::new(catalog(), HashSet::new()))
        .build()
        .unwrap()
}
fn step(p: &mut Pgso<Scripted, LocalActuator>) -> Catalog {
    p.process_window(&AudioWindow {
        samples: vec![],
        sample_rate: 16000,
        timestamp_ms: 0,
    })
    .unwrap()
}

#[test]
fn control_valid_nominal_reading_restores_catalog() {
    let mut p = pipeline(
        vec![vec![reading(0.95, 0.9, 1)], vec![reading(0.5, 0.9, 2)]],
        vec![prune()],
    );
    assert!(!step(&mut p).contains(&ToolId::from("quote")));
    assert!(step(&mut p).contains(&ToolId::from("quote")));
}

#[test]
fn abstention_after_activation_must_hold_existing_restriction() {
    let mut p = pipeline(
        vec![vec![reading(0.95, 0.9, 1)], vec![reading(0.95, 0.1, 2)]],
        vec![prune()],
    );
    assert!(!step(&mut p).contains(&ToolId::from("quote")));
    assert!(
        !step(&mut p).contains(&ToolId::from("quote")),
        "low confidence restored the restricted tool"
    );
}

#[test]
fn repeated_local_directive_must_be_idempotent() {
    let mut a = LocalActuator::new(catalog(), HashSet::new());
    let d = ScopeDecision::new(Action::InjectDirective("Clarify.".into()));
    a.apply(&d).unwrap();
    a.apply(&d).unwrap();
    assert_eq!(a.directives().len(), 1, "duplicate directive accumulates");
}

#[test]
fn repeated_mcp_directive_must_be_idempotent() {
    let mut a = McpActuator::new(catalog(), HashSet::new());
    let d = ScopeDecision::new(Action::InjectDirective("Clarify.".into()));
    a.apply(&d).unwrap();
    a.apply(&d).unwrap();
    assert_eq!(a.directives().len(), 1, "duplicate directive accumulates");
}

#[test]
fn directive_only_recovery_must_be_audited() {
    let mut p = pipeline(
        vec![vec![reading(0.95, 0.9, 1)], vec![reading(0.5, 0.9, 2)]],
        vec![directive()],
    );
    step(&mut p);
    assert_eq!(p.actuator().directives().len(), 1);
    step(&mut p);
    assert!(p.actuator().directives().is_empty());
    assert_eq!(
        p.audit_log().entries().len(),
        2,
        "directive removal is absent from audit"
    );
}

#[test]
fn ceased_strong_rule_must_not_survive_a_milder_rule() {
    let mut p = pipeline(
        vec![vec![reading(0.95, 0.9, 1)], vec![reading(0.75, 0.9, 2)]],
        vec![
            rule("strong", 0.4, Action::Prune(ToolId::from("quote"))),
            directive(),
        ],
    );
    assert!(!step(&mut p).contains(&ToolId::from("quote")));
    assert!(
        step(&mut p).contains(&ToolId::from("quote")),
        "expired stronger restriction remains active"
    );
}

#[test]
fn equivalent_reading_sequence_must_not_depend_on_batch_boundaries() {
    let high = reading(0.95, 0.9, 1);
    let calm = reading(0.5, 0.9, 2);
    let mut grouped = pipeline(vec![vec![high.clone(), calm.clone()]], vec![prune()]);
    let mut separate = pipeline(vec![vec![high], vec![calm]], vec![prune()]);
    let grouped_final = step(&mut grouped);
    step(&mut separate);
    let separate_final = step(&mut separate);
    assert_eq!(
        grouped_final, separate_final,
        "batch boundaries change final policy"
    );
}

#[test]
fn nan_confidence_must_not_trigger() {
    let mut e = DecisionEngine::new(config());
    assert!(
        e.process(&reading(0.95, f32::NAN, 1)).is_none(),
        "NaN bypasses the confidence gate"
    );
}

#[test]
fn nonfinite_reading_must_not_poison_future_valid_readings() {
    let mut e = DecisionEngine::new(config());
    e.process(&reading(f32::NAN, 0.9, 1));
    assert!(
        e.process(&reading(0.95, 0.9, 2)).is_some(),
        "NaN poisons the baseline"
    );
}

#[test]
fn step_up_change_must_not_be_indistinguishable_over_the_only_mcp_payload() {
    // This exposes a transport contract gap, not a demonstrated tools/call exploit.
    let mut a = McpActuator::new(catalog(), HashSet::new());
    let before = a.tools_list_response();
    a.apply(&ScopeDecision::new(Action::RequireStepUp(ToolId::from(
        "quote",
    ))))
    .unwrap();
    assert!(
        a.current_catalog()
            .find(&ToolId::from("quote"))
            .unwrap()
            .requires_step_up
    );
    assert_ne!(
        before,
        a.tools_list_response(),
        "step-up is only local state; wire payload unchanged"
    );
}

#[test]
fn control_protected_tool_remains_available() {
    let mut a = McpActuator::new(catalog(), HashSet::from([ToolId::from("human")]));
    a.apply(&ScopeDecision::new(Action::Prune(ToolId::from("human"))))
        .unwrap();
    assert!(a.current_catalog().contains(&ToolId::from("human")));
}

#[test]
fn independent_axis_contributions_survive_recovery_and_expire_explicitly() {
    let mut second = reading(0.95, 0.9, 2);
    second.axis = Axis::Valence;
    let mut p = pipeline(
        vec![
            vec![reading(0.95, 0.9, 1), second],
            vec![reading(0.5, 0.9, 3)],
        ],
        vec![
            directive(),
            Rule::new(
                "valence",
                Axis::Valence,
                0.2,
                0.5,
                vec![Action::InjectDirective("Clarify.".into())],
            ),
        ],
    );
    step(&mut p);
    assert_eq!(p.actuator().directives(), &["Clarify."]);
    step(&mut p);
    assert_eq!(p.actuator().directives(), &["Clarify."]);
    p.expire_before(3).unwrap();
    assert!(p.actuator().directives().is_empty());
    assert!(p
        .audit_log()
        .transitions()
        .last()
        .unwrap()
        .reading
        .is_none());
}

#[test]
fn invalid_configuration_and_stale_readings_cannot_change_policy() {
    let mut bad = config();
    bad.ema_alpha = f32::NAN;
    assert!(DecisionEngine::try_new(bad.clone()).is_err());
    assert!(DecisionEngine::new(bad)
        .process(&reading(0.95, 0.9, 1))
        .is_none());
    let mut p = pipeline(
        vec![vec![reading(0.95, 0.9, 20)], vec![reading(0.5, 0.9, 10)]],
        vec![prune()],
    );
    step(&mut p);
    assert!(!step(&mut p).contains(&ToolId::from("quote")));
}

#[test]
fn directional_rule_distinguishes_increases_from_decreases() {
    let mut p = pipeline(
        vec![vec![reading(0.05, 0.9, 1)], vec![reading(0.95, 0.9, 2)]],
        vec![prune().with_direction(pgso_core::Direction::Rising)],
    );
    assert!(step(&mut p).contains(&ToolId::from("quote")));
    assert!(!step(&mut p).contains(&ToolId::from("quote")));
}

#[test]
fn failed_reconciliation_does_not_advance_engine_or_commit_policy() {
    struct Fallible {
        inner: LocalActuator,
        fail: bool,
    }
    impl Actuator for Fallible {
        fn current_state(&self) -> pgso_core::GovernanceState {
            self.inner.current_state()
        }
        fn current_catalog(&self) -> Catalog {
            self.inner.current_catalog()
        }
        fn apply(&mut self, d: &ScopeDecision) -> Result<Catalog, pgso_core::ActuatorError> {
            self.inner.apply(d)
        }
        fn reconcile(
            &mut self,
            active: &[ScopeDecision],
        ) -> Result<Catalog, pgso_core::ActuatorError> {
            if self.fail {
                self.fail = false;
                return Err(pgso_core::ActuatorError::Internal(
                    "injected failure".into(),
                ));
            }
            self.inner.reconcile(active)
        }
    }
    let mut cfg = config();
    cfg.ema_alpha = 1.0;
    let mut p = Pgso::builder()
        .signal(Scripted(
            vec![vec![reading(0.95, 0.9, 1)], vec![reading(0.95, 0.9, 1)]].into(),
        ))
        .engine(DecisionEngine::new(cfg))
        .rules(RuleEngine::new(RuleSet::new(vec![prune()]), HashSet::new()))
        .actuator(Fallible {
            inner: LocalActuator::new(catalog(), HashSet::new()),
            fail: true,
        })
        .build()
        .unwrap();
    let audio = AudioWindow {
        samples: vec![],
        sample_rate: 16000,
        timestamp_ms: 1,
    };
    assert!(p.process_window(&audio).is_err());
    assert!(p.current_catalog().contains(&ToolId::from("quote")));
    assert!(p.audit_log().transitions().is_empty());
    // If the failed reading updated the EMA, its identical retry would be nominal.
    assert!(!p
        .process_window(&audio)
        .unwrap()
        .contains(&ToolId::from("quote")));
    assert_eq!(p.audit_log().transitions().len(), 1);
}
