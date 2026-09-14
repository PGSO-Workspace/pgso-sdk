//! Boundary contracts exposed by the mutation campaign; expected values are explicit.
use pgso_core::*;
use std::collections::HashSet;
fn config() -> EngineConfig {
    EngineConfig {
        confidence_threshold: 0.5,
        deviation_threshold: 0.25,
        hysteresis_window: 2,
        ema_alpha: 0.5,
        warmup_readings: 2,
        population_prior: 0.5,
    }
}
fn reading(value: f32, ts: u64) -> SignalReading {
    SignalReading {
        value,
        axis: Axis::Arousal,
        confidence: 0.5,
        timestamp_ms: ts,
    }
}
#[test]
fn invalid_and_zero_thresholds_have_distinct_configuration_outcomes() {
    for threshold in [-0.1, f32::NAN, f32::INFINITY] {
        let mut c = config();
        c.deviation_threshold = threshold;
        assert!(c.validate().is_err());
        assert!(DecisionEngine::new(c).validate().is_err());
    }
    let mut c = config();
    c.deviation_threshold = 0.0;
    assert!(DecisionEngine::try_new(c).is_ok());
    let rule = |id: &str, d, c| {
        Rule::new(
            id,
            Axis::Arousal,
            d,
            c,
            vec![Action::InjectDirective("Clarify".into())],
        )
    };
    for rules in [
        vec![rule("", 0.25, 0.5)],
        vec![rule("a", 0.25, 0.5), rule("a", 0.25, 0.5)],
        vec![rule("a", -0.1, 0.5)],
        vec![rule("a", f32::NAN, 0.5)],
        vec![rule("a", f32::INFINITY, 0.5)],
        vec![rule("a", 0.25, f32::NAN)],
        vec![rule("a", 0.25, 1.1)],
    ] {
        assert!(RuleEngine::new(RuleSet::new(rules), HashSet::new())
            .validate()
            .is_err());
    }
    assert!(
        RuleEngine::new(RuleSet::new(vec![rule("a", 0.0, 0.0)]), HashSet::new())
            .validate()
            .is_ok()
    );
}
#[test]
fn exact_confidence_equal_timestamps_and_gap_boundary_are_accepted() {
    let mut e = DecisionEngine::new(config()).with_max_gap_ms(100).unwrap();
    assert!(e.process(&reading(1.0, 0)).is_none());
    assert!(e.process(&reading(1.0, 100)).is_some());
    let mut e = DecisionEngine::new(config());
    assert!(e.process(&reading(1.0, 0)).is_none());
    assert!(e.process(&reading(1.0, 0)).is_some()); // millisecond timestamps may coincide
}
#[test]
fn nominal_warmup_is_mean_then_ema() {
    let mut c = config();
    c.hysteresis_window = 1;
    let mut e = DecisionEngine::new(c);
    assert!(e.process(&reading(0.4, 0)).is_none());
    assert!(e.process(&reading(0.5, 1)).is_none()); // mean=.45
    assert!(e.process(&reading(0.6, 2)).is_none()); // EMA=.525
    let out = e.process(&reading(1.0, 3)).unwrap();
    assert!((out.baseline - 0.525).abs() < 1e-6);
}
#[test]
fn zero_threshold_and_neutral_observation_follow_declared_inclusive_gate() {
    let mut c = config();
    c.deviation_threshold = 0.0;
    let mut e = DecisionEngine::new(c);
    assert!(e.process(&reading(0.5, 0)).is_none());
    assert!(e.process(&reading(0.0, 1)).is_some());
}
#[test]
fn directional_rules_exclude_equality_and_select_each_side() {
    for (direction, expected) in [
        (Direction::Rising, [false, false, true]),
        (Direction::Falling, [true, false, false]),
    ] {
        let r = Rule::new(
            "direction",
            Axis::Arousal,
            0.0,
            0.5,
            vec![Action::InjectDirective("Clarify".into())],
        )
        .with_direction(direction);
        let rules = RuleEngine::new(RuleSet::new(vec![r]), HashSet::new());
        for (value, matches) in [0.0f32, 0.5, 1.0].into_iter().zip(expected) {
            let out = EngineOutput {
                axis: Axis::Arousal,
                raw_value: value,
                deviation: (value - 0.5).abs(),
                baseline: 0.5,
                confidence: 0.5,
                timestamp_ms: 1,
            };
            assert_eq!(!rules.evaluate(&out).is_empty(), matches);
        }
    }
}
