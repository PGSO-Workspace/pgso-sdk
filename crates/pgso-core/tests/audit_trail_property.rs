//! OE4-I2 / G5: completeness of the audit trail, checked as a property.
//!
//! For ANY rule set, engine configuration and interleaving of observation windows and trusted-host
//! expiries, the committed transitions in [`AuditLog::transitions`] must:
//!
//! 1. account for every change of the effective policy state (catalog and directives): replaying
//!    the trail from the nominal state reaches exactly the state the actuator serves, after every step;
//! 2. chain without gaps (`after` of one transition is `before` of the next) and contain no no-op;
//! 3. carry a cause consistent with the event that produced it (observation versus host expiry,
//!    activation versus retirement), and contributions that name their rule, axis and deviation.
//!
//! Failed attempts and abstentions are, by contract, not transitions; the property therefore
//! speaks about policy changes, not about every reading.

use pgso_actuator_local::LocalActuator;
use pgso_core::*;
use proptest::prelude::*;
use std::collections::{HashSet, VecDeque};

struct Replay(VecDeque<Vec<SignalReading>>);

impl Signal for Replay {
    fn extract(&mut self, _: &AudioWindow) -> Vec<SignalReading> {
        self.0.pop_front().unwrap_or_default()
    }
}

#[derive(Debug, Clone)]
enum Step {
    Window(Vec<SignalReading>),
    /// Expire evidence older than `now - age_ms` at host time `now` (the latest timestamp seen).
    Expire(u64),
}

fn arb_axis() -> impl Strategy<Value = Axis> {
    prop_oneof![Just(Axis::Arousal), Just(Axis::Valence)]
}

fn arb_tool() -> impl Strategy<Value = &'static str> {
    prop_oneof![Just("quote"), Just("refund"), Just("escalate")]
}

fn arb_action() -> impl Strategy<Value = Action> {
    prop_oneof![
        arb_tool().prop_map(|t| Action::RequireStepUp(ToolId::from(t))),
        arb_tool().prop_map(|t| Action::Prune(ToolId::from(t))),
        prop_oneof![Just("Confirm before acting."), Just("Slow down.")]
            .prop_map(|t| Action::InjectDirective(t.to_string())),
    ]
}

fn arb_direction() -> impl Strategy<Value = Option<Direction>> {
    prop_oneof![
        Just(None),
        Just(Some(Direction::Rising)),
        Just(Some(Direction::Falling))
    ]
}

const RULE_IDS: [&str; 4] = ["rule_0", "rule_1", "rule_2", "rule_3"];

fn arb_rules() -> impl Strategy<Value = Vec<Rule>> {
    prop::collection::vec(
        (
            arb_axis(),
            0.0f32..0.6,
            0.0f32..0.9,
            prop::collection::vec(arb_action(), 1..3),
            arb_direction(),
        ),
        1..5,
    )
    .prop_map(|specs| {
        specs
            .into_iter()
            .enumerate()
            .map(|(i, (axis, dev, conf, actions, dir))| {
                let rule = Rule::new(RULE_IDS[i], axis, dev, conf, actions);
                match dir {
                    Some(d) => rule.with_direction(d),
                    None => rule,
                }
            })
            .collect()
    })
}

fn arb_config() -> impl Strategy<Value = EngineConfig> {
    (0.3f32..0.7, 0.05f32..0.4, 1u32..4, 0.0f32..0.3, 0u64..3).prop_map(
        |(conf, dev, h, alpha, warm)| EngineConfig {
            confidence_threshold: conf,
            deviation_threshold: dev,
            hysteresis_window: h,
            ema_alpha: alpha,
            warmup_readings: warm,
            population_prior: 0.5,
        },
    )
}

/// Steps with mostly increasing timestamps, plus occasional out-of-order or low-confidence readings.
fn arb_steps() -> impl Strategy<Value = Vec<Step>> {
    prop::collection::vec(
        prop_oneof![
            8 => prop::collection::vec((arb_axis(), 0.0f32..1.0, 0.0f32..1.0, 0u64..700), 0..4)
                .prop_map(|rs| Step::Window(
                    rs.into_iter()
                        .map(|(axis, value, confidence, dt)| SignalReading {
                            value,
                            axis,
                            confidence,
                            // Placeholder: made cumulative below.
                            timestamp_ms: dt,
                        })
                        .collect(),
                )),
            1 => (0u64..3000).prop_map(Step::Expire),
        ],
        1..40,
    )
    .prop_map(|steps| {
        // Turn per-reading offsets into a mostly increasing clock; an offset of 0 repeats the
        // previous timestamp, and every seventh reading is sent back in time.
        let mut clock = 0u64;
        let mut k = 0usize;
        steps
            .into_iter()
            .map(|s| match s {
                Step::Window(rs) => Step::Window(
                    rs.into_iter()
                        .map(|mut r| {
                            k += 1;
                            if k % 7 == 0 {
                                r.timestamp_ms = clock.saturating_sub(r.timestamp_ms);
                            } else {
                                clock += r.timestamp_ms;
                                r.timestamp_ms = clock;
                            }
                            r
                        })
                        .collect(),
                ),
                Step::Expire(age) => Step::Expire(age),
            })
            .collect()
    })
}

fn catalog() -> Catalog {
    Catalog::new(vec![
        Tool::new("quote", "Quote a price"),
        Tool::new("refund", "Issue a refund"),
        Tool::new("escalate", "Hand over to a human"),
    ])
}

fn window() -> AudioWindow {
    AudioWindow {
        samples: vec![],
        sample_rate: 16_000,
        timestamp_ms: 0,
    }
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 1024, .. ProptestConfig::default() })]

    #[test]
    fn prop_audit_trail_accounts_for_every_policy_change(
        rules in arb_rules(),
        config in arb_config(),
        steps in arb_steps(),
    ) {
        let protected = HashSet::from([ToolId::from("escalate")]);
        let windows: VecDeque<Vec<SignalReading>> = steps
            .iter()
            .filter_map(|s| match s {
                Step::Window(rs) => Some(rs.clone()),
                Step::Expire(_) => None,
            })
            .collect();
        let mut p = Pgso::builder()
            .signal(Replay(windows))
            .engine(DecisionEngine::try_new(config).unwrap().with_max_gap_ms(1500).unwrap())
            .rules(RuleEngine::new(RuleSet::new(rules), protected.clone()))
            .actuator(LocalActuator::new(catalog(), protected))
            .build()
            .unwrap();

        let nominal = GovernanceState::nominal(catalog());
        prop_assert_eq!(p.actuator().current_state(), nominal.clone());
        let mut now = 0u64;

        for step in &steps {
            let before_state = p.actuator().current_state();
            let before_len = p.audit_log().transitions().len();
            match step {
                Step::Window(rs) => {
                    now = now.max(rs.iter().map(|r| r.timestamp_ms).max().unwrap_or(0));
                    let _ = p.process_window(&window()).unwrap();
                }
                Step::Expire(age) => {
                    let _ = p.expire_before(now.saturating_sub(*age), now).unwrap();
                }
            }
            let after_state = p.actuator().current_state();
            let new = &p.audit_log().transitions()[before_len..];

            // (1) Every change of the served state is covered by transitions of this step.
            if before_state != after_state {
                prop_assert!(!new.is_empty(), "unrecorded policy change");
            }
            if let (Some(first), Some(last)) = (new.first(), new.last()) {
                prop_assert_eq!(&first.before, &before_state);
                prop_assert_eq!(&last.after, &after_state);
            }

            // (3) Cause consistent with the event, and contributions that explain themselves.
            for t in new {
                match step {
                    Step::Expire(age) => {
                        prop_assert_eq!(t.cause, TransitionCause::HostExpiry);
                        prop_assert!(t.reading.is_none());
                        prop_assert_eq!(t.expiry_cutoff_ms, Some(now.saturating_sub(*age)));
                        for d in &t.active {
                            prop_assert!(d.audit.timestamp_ms >= now.saturating_sub(*age));
                        }
                    }
                    Step::Window(_) => {
                        prop_assert!(t.cause != TransitionCause::HostExpiry);
                        prop_assert!(t.expiry_cutoff_ms.is_none());
                        let reading = t.reading.as_ref().expect("observation transitions carry their reading");
                        prop_assert_eq!(t.timestamp_ms, reading.timestamp_ms);
                        let on_axis = t.active.iter().any(|d| d.audit.axis == Some(reading.axis));
                        if t.cause.is_retirement() {
                            prop_assert!(!on_axis, "retirement left a contribution on the reading's axis");
                        } else {
                            prop_assert!(on_axis, "activation without a contribution on the reading's axis");
                        }
                    }
                }
                for d in &t.active {
                    prop_assert!(d.audit.rule_id.is_some());
                    prop_assert!(d.audit.axis.is_some());
                    prop_assert!(d.audit.deviation.is_some());
                    prop_assert!(d.audit.threshold_crossed.is_some());
                }
            }
        }

        // (2) The whole trail chains from the nominal state to the served state, without no-ops.
        let mut replayed = nominal;
        for t in p.audit_log().transitions() {
            prop_assert_eq!(&t.before, &replayed);
            prop_assert!(t.before != t.after, "no-op recorded as a transition");
            replayed = t.after.clone();
        }
        prop_assert_eq!(replayed, p.actuator().current_state());
    }
}
