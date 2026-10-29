//! Contribution provenance is audit-only: activating vs latest renewing reading.
use pgso_actuator_local::LocalActuator;
use pgso_core::*;
use std::collections::{HashSet, VecDeque};

struct Replay(VecDeque<Vec<SignalReading>>);

impl Signal for Replay {
    fn extract(&mut self, _: &AudioWindow) -> Vec<SignalReading> {
        self.0.pop_front().unwrap_or_default()
    }
}

type P = Pgso<Replay, LocalActuator>;

fn r(axis: Axis, value: f32, ts: u64) -> SignalReading {
    SignalReading {
        value,
        axis,
        confidence: 0.9,
        timestamp_ms: ts,
    }
}

fn high(ts: u64) -> Vec<SignalReading> {
    vec![r(Axis::Arousal, 0.95, ts)]
}

fn rule(id: &str, axis: Axis, min_deviation: f32, tool: &str) -> Rule {
    Rule::new(
        id,
        axis,
        min_deviation,
        0.5,
        vec![Action::Prune(ToolId::from(tool))],
    )
    .with_direction(Direction::Rising)
}

fn pipeline(windows: Vec<Vec<SignalReading>>) -> P {
    pipeline_with(
        windows,
        vec![
            rule("arousal_rule", Axis::Arousal, 0.3, "quote"),
            rule("valence_rule", Axis::Valence, 0.3, "refund"),
        ],
        LocalActuator::new(
            Catalog::new(vec![Tool::new("quote", "q"), Tool::new("refund", "r")]),
            HashSet::new(),
        ),
    )
}

fn pipeline_with<A: Actuator>(
    windows: Vec<Vec<SignalReading>>,
    rules: Vec<Rule>,
    actuator: A,
) -> Pgso<Replay, A> {
    Pgso::builder()
        .signal(Replay(windows.into()))
        .engine(
            DecisionEngine::try_new(EngineConfig {
                population_prior: 0.5,
                ema_alpha: 0.0,
                warmup_readings: 0,
                hysteresis_window: 3,
                deviation_threshold: 0.3,
                confidence_threshold: 0.5,
            })
            .unwrap()
            .with_max_gap_ms(1200)
            .unwrap(),
        )
        .rules(RuleEngine::new(RuleSet::new(rules), HashSet::new()))
        .actuator(actuator)
        .build()
        .unwrap()
}

fn step<A: Actuator>(p: &mut Pgso<Replay, A>, n: usize) {
    for _ in 0..n {
        let _ = p
            .process_window(&AudioWindow {
                samples: vec![],
                sample_rate: 16_000,
                timestamp_ms: 0,
            })
            .unwrap();
    }
}

fn times<A: Actuator>(p: &Pgso<Replay, A>, axis: Axis) -> Option<(u64, u64, Vec<String>)> {
    p.contributions().find(|c| c.axis == axis).map(|c| {
        (
            c.activated.timestamp_ms,
            c.renewed.timestamp_ms,
            c.rule_ids.clone(),
        )
    })
}

#[test]
fn activation_records_the_same_reading_twice() {
    let mut p = pipeline(vec![high(400), high(800), high(1200)]);
    step(&mut p, 2);
    assert!(
        times(&p, Axis::Arousal).is_none(),
        "pending readings create no contribution"
    );
    step(&mut p, 1);
    assert_eq!(
        times(&p, Axis::Arousal),
        Some((1200, 1200, vec!["arousal_rule".into()]))
    );
    let transition = p.audit_log().transitions().last().unwrap();
    assert_eq!(transition.reading.as_ref().unwrap().timestamp_ms, 1200);
}

#[test]
fn maintenance_renews_without_rewriting_activation_or_transitions() {
    let mut p = pipeline(vec![
        high(400),
        high(800),
        high(1200),
        high(1600),
        vec![],
        vec![r(Axis::Arousal, 0.95, 2000)],
    ]);
    step(&mut p, 6);
    assert_eq!(
        times(&p, Axis::Arousal).map(|t| (t.0, t.1)),
        Some((1200, 2000))
    );
    assert_eq!(
        p.audit_log().transitions().len(),
        1,
        "renewal is not a policy transition"
    );
    assert_eq!(
        p.audit_log().transitions()[0]
            .reading
            .as_ref()
            .unwrap()
            .timestamp_ms,
        1200
    );
}

#[test]
fn pending_low_confidence_and_gap_readings_do_not_renew() {
    let mut low = r(Axis::Arousal, 0.95, 1600);
    low.confidence = 0.1;
    let mut p = pipeline(vec![
        high(400),
        high(800),
        high(1200),
        vec![low],
        high(2401),
        high(2801),
    ]);
    step(&mut p, 5);
    assert_eq!(
        times(&p, Axis::Arousal).map(|t| t.1),
        Some(1200),
        "abstention and gap-reset pending hold"
    );
    step(&mut p, 1);
    assert_eq!(
        times(&p, Axis::Arousal).map(|t| t.1),
        Some(1200),
        "second pending reading still holds"
    );
}

#[test]
fn expiry_uses_renewal_and_clears_provenance() {
    let mut p = pipeline(vec![high(400), high(800), high(1200), high(1600)]);
    step(&mut p, 4);
    p.expire_before(1600, 1700).unwrap();
    assert_eq!(
        times(&p, Axis::Arousal).map(|t| (t.0, t.1)),
        Some((1200, 1600)),
        "activation older than cutoff, renewal not: still active"
    );
    assert!(!p.current_catalog().contains(&ToolId::from("quote")));
    p.expire_before(1601, 1700).unwrap();
    assert!(times(&p, Axis::Arousal).is_none());
    assert!(p.current_catalog().contains(&ToolId::from("quote")));
}

#[test]
fn recovery_clears_and_reactivation_starts_new_provenance() {
    let mut p = pipeline(vec![
        high(400),
        high(800),
        high(1200),
        vec![r(Axis::Arousal, 0.5, 1600)],
        high(2000),
        high(2400),
        high(2800),
    ]);
    step(&mut p, 4);
    assert!(times(&p, Axis::Arousal).is_none());
    step(&mut p, 3);
    assert_eq!(
        times(&p, Axis::Arousal).map(|t| (t.0, t.1)),
        Some((2800, 2800))
    );
}

#[test]
fn multiple_contributions_are_tracked_and_expired_independently() {
    let both = |ts| vec![r(Axis::Arousal, 0.95, ts), r(Axis::Valence, 0.95, ts)];
    let mut p = pipeline(vec![both(400), both(800), both(1200), high(1600)]);
    step(&mut p, 4);
    assert_eq!(
        times(&p, Axis::Arousal).map(|t| (t.0, t.1)),
        Some((1200, 1600))
    );
    assert_eq!(
        times(&p, Axis::Valence),
        Some((1200, 1200, vec!["valence_rule".into()]))
    );
    p.expire_before(1201, 1700).unwrap();
    assert!(times(&p, Axis::Valence).is_none());
    assert_eq!(times(&p, Axis::Arousal).map(|t| t.1), Some(1600));
    let catalog = p.current_catalog();
    assert!(catalog.contains(&ToolId::from("refund")) && !catalog.contains(&ToolId::from("quote")));
}

#[test]
fn falling_trigger_retires_contribution_and_provenance() {
    let low = |ts| vec![r(Axis::Arousal, 0.05, ts)];
    let mut p = pipeline(vec![high(400), high(800), high(1200), low(1600), low(2000)]);
    step(&mut p, 5);
    assert!(
        times(&p, Axis::Arousal).is_some(),
        "pending falling readings hold"
    );
    let mut p = pipeline(vec![
        high(400),
        high(800),
        high(1200),
        low(1600),
        low(2000),
        low(2400),
    ]);
    step(&mut p, 6);
    assert!(times(&p, Axis::Arousal).is_none());
}

#[test]
fn changed_rule_set_starts_a_new_contribution() {
    let rules = vec![
        rule("low", Axis::Arousal, 0.3, "quote"),
        rule("high", Axis::Arousal, 0.45, "refund"),
    ];
    let windows = vec![
        vec![r(Axis::Arousal, 0.85, 400)],
        vec![r(Axis::Arousal, 0.85, 800)],
        vec![r(Axis::Arousal, 0.85, 1200)],
        vec![r(Axis::Arousal, 0.85, 1600)],
        vec![r(Axis::Arousal, 0.99, 2000)],
        vec![r(Axis::Arousal, 0.99, 2400)],
    ];
    let actuator = LocalActuator::new(
        Catalog::new(vec![Tool::new("quote", "q"), Tool::new("refund", "r")]),
        HashSet::new(),
    );
    let mut p = pipeline_with(windows, rules, actuator);
    step(&mut p, 4);
    assert_eq!(
        times(&p, Axis::Arousal),
        Some((1200, 1600, vec!["low".into()]))
    );
    step(&mut p, 2);
    assert_eq!(
        times(&p, Axis::Arousal),
        Some((2000, 2400, vec!["low".into(), "high".into()]))
    );
}

#[test]
fn several_readings_in_one_window_are_processed_in_order() {
    let mut p = pipeline(vec![
        vec![r(Axis::Arousal, 0.95, 400), r(Axis::Arousal, 0.95, 800)],
        vec![r(Axis::Arousal, 0.95, 1200)],
        vec![r(Axis::Arousal, 0.95, 1600), r(Axis::Arousal, 0.95, 2000)],
    ]);
    step(&mut p, 3);
    assert_eq!(
        times(&p, Axis::Arousal).map(|t| (t.0, t.1)),
        Some((1200, 2000))
    );
}

struct Flaky {
    inner: LocalActuator,
    fail: std::rc::Rc<std::cell::Cell<bool>>,
}

impl Actuator for Flaky {
    fn current_state(&self) -> GovernanceState {
        self.inner.current_state()
    }
    fn reconcile(&mut self, active: &[ScopeDecision]) -> Result<Catalog, ActuatorError> {
        if self.fail.get() {
            return Err(ActuatorError::Internal("injected".into()));
        }
        self.inner.reconcile(active)
    }
    fn current_catalog(&self) -> Catalog {
        self.inner.current_catalog()
    }
    fn apply(&mut self, decision: &ScopeDecision) -> Result<Catalog, ActuatorError> {
        self.inner.apply(decision)
    }
}

#[test]
fn failed_commits_leave_provenance_unchanged() {
    let fail = std::rc::Rc::new(std::cell::Cell::new(false));
    let actuator = Flaky {
        inner: LocalActuator::new(
            Catalog::new(vec![Tool::new("quote", "q"), Tool::new("refund", "r")]),
            HashSet::new(),
        ),
        fail: std::rc::Rc::clone(&fail),
    };
    let rules = vec![rule("arousal_rule", Axis::Arousal, 0.3, "quote")];
    let mut p = pipeline_with(
        vec![high(400), high(800), high(1200), high(1600)],
        rules,
        actuator,
    );
    step(&mut p, 3);
    fail.set(true);
    assert!(p
        .process_window(&AudioWindow {
            samples: vec![],
            sample_rate: 16_000,
            timestamp_ms: 0
        })
        .is_err());
    assert_eq!(
        times(&p, Axis::Arousal).map(|t| (t.0, t.1)),
        Some((1200, 1200))
    );
    assert!(p.expire_before(1201, 1700).is_err());
    assert_eq!(
        times(&p, Axis::Arousal).map(|t| (t.0, t.1)),
        Some((1200, 1200))
    );
    assert!(!p.current_catalog().contains(&ToolId::from("quote")));
}
