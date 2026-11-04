//! PolicyTransition::cause is recorded at the triggering event, never inferred afterwards.
use pgso_actuator_local::LocalActuator;
use pgso_core::*;
use std::collections::{HashSet, VecDeque};

struct Replay(VecDeque<Vec<SignalReading>>);

impl Signal for Replay {
    fn extract(&mut self, _: &AudioWindow) -> Vec<SignalReading> {
        self.0.pop_front().unwrap_or_default()
    }
}

fn r(value: f32, ts: u64) -> SignalReading {
    SignalReading {
        value,
        axis: Axis::Arousal,
        confidence: 0.9,
        timestamp_ms: ts,
    }
}

fn rule(id: &str, min_deviation: f32, tool: &str) -> Rule {
    Rule::new(
        id,
        Axis::Arousal,
        min_deviation,
        0.5,
        vec![Action::RequireStepUp(ToolId::from(tool))],
    )
    .with_direction(Direction::Rising)
}

fn pipeline(values: &[(f32, u64)], rules: Vec<Rule>) -> Pgso<Replay, LocalActuator> {
    Pgso::builder()
        .signal(Replay(values.iter().map(|&(v, t)| vec![r(v, t)]).collect()))
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
        .actuator(LocalActuator::new(
            Catalog::new(vec![Tool::new("quote", "q"), Tool::new("refund", "r")]),
            HashSet::new(),
        ))
        .build()
        .unwrap()
}

fn run(values: &[(f32, u64)], rules: Vec<Rule>) -> Pgso<Replay, LocalActuator> {
    let mut p = pipeline(values, rules);
    for _ in values {
        let _ = p
            .process_window(&AudioWindow {
                samples: vec![],
                sample_rate: 16_000,
                timestamp_ms: 0,
            })
            .unwrap();
    }
    p
}

fn causes(p: &Pgso<Replay, LocalActuator>) -> Vec<TransitionCause> {
    p.audit_log()
        .transitions()
        .iter()
        .map(|t| t.cause)
        .collect()
}

const HIGH: [(f32, u64); 3] = [(0.95, 400), (0.95, 800), (0.95, 1200)];

#[test]
fn activation_then_nominal_reading() {
    let mut values = HIGH.to_vec();
    values.push((0.5, 1600));
    let p = run(&values, vec![rule("elevated", 0.3, "quote")]);
    assert_eq!(
        causes(&p),
        [TransitionCause::Activation, TransitionCause::NominalReading]
    );
    let retire = &p.audit_log().transitions()[1];
    assert_eq!(retire.reading.as_ref().map(|r| r.timestamp_ms), Some(1600));
    assert!(retire.active.is_empty() && retire.cause.is_retirement());
}

#[test]
fn renewals_create_no_transition() {
    let mut values = HIGH.to_vec();
    values.extend([(0.95, 1600), (0.95, 2000)]);
    let p = run(&values, vec![rule("elevated", 0.3, "quote")]);
    assert_eq!(causes(&p), [TransitionCause::Activation]);
}

#[test]
fn opposite_direction_run_retires_as_no_matching_rule() {
    let mut values = HIGH.to_vec();
    values.extend([(0.05, 1600), (0.05, 2000), (0.05, 2400)]);
    let p = run(&values, vec![rule("elevated", 0.3, "quote")]);
    assert_eq!(
        causes(&p),
        [TransitionCause::Activation, TransitionCause::NoMatchingRule]
    );
    assert_eq!(
        p.audit_log().transitions()[1]
            .reading
            .as_ref()
            .map(|r| r.timestamp_ms),
        Some(2400)
    );
}

#[test]
fn host_expiry_is_recorded_with_its_cutoff() {
    let mut p = run(&HIGH, vec![rule("elevated", 0.3, "quote")]);
    let _ = p.expire_before(1000, 2000).unwrap(); // contribution renewed at 1200: survives
    assert_eq!(causes(&p), [TransitionCause::Activation]);
    let _ = p.expire_before(1300, 2500).unwrap();
    assert_eq!(
        causes(&p),
        [TransitionCause::Activation, TransitionCause::HostExpiry]
    );
    let t = &p.audit_log().transitions()[1];
    assert_eq!(
        (t.expiry_cutoff_ms, t.reading.is_none(), t.timestamp_ms),
        (Some(1300), true, 2500)
    );
}

#[test]
fn changed_rule_set_is_a_replacement() {
    let values = [
        (0.85, 400),
        (0.85, 800),
        (0.85, 1200),
        (0.99, 1600),
        (0.85, 2000),
    ];
    let p = run(
        &values,
        vec![rule("low", 0.3, "quote"), rule("high", 0.45, "refund")],
    );
    assert_eq!(
        causes(&p),
        [
            TransitionCause::Activation,
            TransitionCause::Replacement,
            TransitionCause::Replacement
        ]
    );
    assert!(p
        .audit_log()
        .transitions()
        .iter()
        .all(|t| !t.cause.is_retirement()));
}

#[test]
fn cause_names_are_stable() {
    let names: Vec<&str> = [
        TransitionCause::Activation,
        TransitionCause::Replacement,
        TransitionCause::NominalReading,
        TransitionCause::NoMatchingRule,
        TransitionCause::HostExpiry,
    ]
    .iter()
    .map(|c| c.as_str())
    .collect();
    assert_eq!(
        names,
        [
            "activation",
            "replacement",
            "nominal_reading",
            "no_matching_rule",
            "host_expiry"
        ]
    );
}
