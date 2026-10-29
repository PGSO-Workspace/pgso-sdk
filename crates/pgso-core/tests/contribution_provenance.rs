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

fn pipeline(windows: Vec<Vec<SignalReading>>) -> P {
    let rule = |id: &str, axis, tool: &str| {
        Rule::new(id, axis, 0.3, 0.5, vec![Action::Prune(ToolId::from(tool))])
            .with_direction(Direction::Rising)
    };
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
        .rules(RuleEngine::new(
            RuleSet::new(vec![
                rule("arousal_rule", Axis::Arousal, "quote"),
                rule("valence_rule", Axis::Valence, "refund"),
            ]),
            HashSet::new(),
        ))
        .actuator(LocalActuator::new(
            Catalog::new(vec![Tool::new("quote", "q"), Tool::new("refund", "r")]),
            HashSet::new(),
        ))
        .build()
        .unwrap()
}

fn step(p: &mut P, n: usize) {
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

fn times(p: &P, axis: Axis) -> Option<(u64, u64, Vec<String>)> {
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
