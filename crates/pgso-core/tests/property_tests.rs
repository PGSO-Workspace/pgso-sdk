//! Property tests for the PGSO deterministic core.
//!
//! The headline is [`prop_allowlist_never_pruned`] (REQ-2.10 / G2): for ANY
//! signal sequence and ANY rule set — including rules that explicitly target
//! protected tools — the full `DecisionEngine` → `RuleEngine` pipeline NEVER
//! emits `Prune(id)` for a protected `id`. Its absence or failure is a blocking
//! defect.

use pgso_core::*;
use proptest::prelude::*;
use std::collections::HashSet;

fn arb_axis() -> impl Strategy<Value = Axis> {
    prop_oneof![Just(Axis::Valence), Just(Axis::Arousal)]
}

fn arb_reading() -> impl Strategy<Value = SignalReading> {
    (0.0f32..1.0, arb_axis(), 0.0f32..1.0, 0u64..10000).prop_map(|(value, axis, confidence, ts)| {
        SignalReading {
            value,
            axis,
            confidence,
            timestamp_ms: ts,
        }
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
    (
        arb_axis(),
        0.0f32..1.0,
        0.0f32..1.0,
        prop::collection::vec(arb_action(), 1..4),
    )
        .prop_map(|(axis, dev, conf, actions)| Rule::new("random_rule", axis, dev, conf, actions))
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
        prop_assert!(!triggered_at_spike, "isolated spike reached hysteresis");
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
