//! OE4-I3 / G1: preservation of the base directive (system prompt), as controlled cases and a property.
//!
//! The SDK never receives the host's base prompt, so G1 is checked at the boundary the SDK controls:
//! the directive blocks it exposes. A reference host composes `base` followed by one demarcated block
//! holding the active directives. The cases show that, for any base prompt and any sequence:
//!
//! * every exposed directive is, byte for byte, the text of an `InjectDirective` action of a rule
//!   whose contribution is active (the SDK neither rewrites nor invents context);
//! * the base prompt is never altered by composition, and when no contribution with a directive is
//!   active the composed prompt equals the base prompt byte for byte, including after host expiry;
//! * a base prompt that already contains a directive's text or the block markers is left untouched.
//!
//! These cases do not show that a language model obeys the directive; that is outside the SDK.

use pgso_actuator_local::LocalActuator;
use pgso_core::*;
use proptest::prelude::*;
use std::collections::{HashSet, VecDeque};

const BEGIN: &str = "<pgso-directives>";
const END: &str = "</pgso-directives>";

/// Reference host composition: the base prompt is copied unchanged and directives go in one block.
fn compose(base: &str, directives: &[String]) -> String {
    if directives.is_empty() {
        return base.to_owned();
    }
    let mut out = String::with_capacity(base.len() + 64);
    out.push_str(base);
    out.push_str("\n\n");
    out.push_str(BEGIN);
    for d in directives {
        out.push('\n');
        out.push_str(d);
    }
    out.push('\n');
    out.push_str(END);
    out
}

struct Replay(VecDeque<Vec<SignalReading>>);

impl Signal for Replay {
    fn extract(&mut self, _: &AudioWindow) -> Vec<SignalReading> {
        self.0.pop_front().unwrap_or_default()
    }
}

fn reading(axis: Axis, value: f32, ts: u64) -> SignalReading {
    SignalReading {
        value,
        axis,
        confidence: 0.9,
        timestamp_ms: ts,
    }
}

fn window() -> AudioWindow {
    AudioWindow {
        samples: vec![],
        sample_rate: 16_000,
        timestamp_ms: 0,
    }
}

fn pipeline(windows: Vec<Vec<SignalReading>>, rules: Vec<Rule>) -> Pgso<Replay, LocalActuator> {
    Pgso::builder()
        .signal(Replay(windows.into()))
        .engine(
            DecisionEngine::try_new(EngineConfig {
                population_prior: 0.5,
                ema_alpha: 0.0,
                warmup_readings: 0,
                hysteresis_window: 2,
                deviation_threshold: 0.3,
                confidence_threshold: 0.5,
            })
            .unwrap(),
        )
        .rules(RuleEngine::new(
            RuleSet::new(rules),
            HashSet::from([ToolId::from("escalate")]),
        ))
        .actuator(LocalActuator::new(
            Catalog::new(vec![Tool::new("quote", "q"), Tool::new("escalate", "e")]),
            HashSet::from([ToolId::from("escalate")]),
        ))
        .build()
        .unwrap()
}

fn directive_rule(id: &str, axis: Axis, text: &str) -> Rule {
    Rule::new(
        id,
        axis,
        0.3,
        0.5,
        vec![Action::InjectDirective(text.to_owned())],
    )
}

/// Exposed directives equal the directive texts of the active contributions, in order and unchanged.
fn expected_directives<S: Signal>(p: &Pgso<S, LocalActuator>) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    if let Some(t) = p.audit_log().transitions().last() {
        for d in &t.active {
            if let Action::InjectDirective(text) = &d.action {
                if !out.contains(text) {
                    out.push(text.clone());
                }
            }
        }
    }
    out
}

const BASES: [&str; 5] = [
    "You are a sales agent. Quote prices; never issue refunds without approval.",
    "",
    "Confirm before acting.",
    "Base with markers <pgso-directives>\nConfirm before acting.\n</pgso-directives> inside.",
    "Unicode: «tarifa» — ñandú 🎧\r\n\ttrailing whitespace   ",
];

#[test]
fn controlled_case_activation_and_recovery_restore_the_base_byte_for_byte() {
    let text = "Confirm before acting.";
    let windows = vec![
        vec![reading(Axis::Arousal, 0.95, 400)],
        vec![reading(Axis::Arousal, 0.95, 800)],
        vec![reading(Axis::Arousal, 0.5, 1200)],
    ];
    for base in BASES {
        let mut p = pipeline(
            windows.clone(),
            vec![directive_rule("r", Axis::Arousal, text)],
        );
        let _ = p.process_window(&window()).unwrap();
        assert_eq!(
            compose(base, p.actuator().directives()),
            base,
            "no directive before persistence"
        );
        let _ = p.process_window(&window()).unwrap();
        assert_eq!(p.actuator().directives(), &[text.to_owned()]);
        let composed = compose(base, p.actuator().directives());
        assert!(composed.as_bytes().starts_with(base.as_bytes()));
        assert_eq!(composed.matches(END).count(), base.matches(END).count() + 1);
        let _ = p.process_window(&window()).unwrap();
        assert!(p.actuator().directives().is_empty());
        assert_eq!(
            compose(base, p.actuator().directives()).as_bytes(),
            base.as_bytes()
        );
    }
}

#[test]
fn controlled_case_two_axes_retire_independently_and_expiry_clears_the_rest() {
    let windows = vec![
        vec![
            reading(Axis::Arousal, 0.95, 400),
            reading(Axis::Valence, 0.95, 410),
        ],
        vec![
            reading(Axis::Arousal, 0.95, 800),
            reading(Axis::Valence, 0.95, 810),
        ],
        vec![reading(Axis::Arousal, 0.5, 1200)],
    ];
    let mut p = pipeline(
        windows,
        vec![
            directive_rule("a", Axis::Arousal, "Slow down."),
            directive_rule("v", Axis::Valence, "Offer a human."),
        ],
    );
    for _ in 0..2 {
        let _ = p.process_window(&window()).unwrap();
    }
    // Both blocks are active, in the deterministic order of the active contributions.
    let mut both = p.actuator().directives().to_vec();
    assert_eq!(both, expected_directives(&p));
    both.sort();
    assert_eq!(both, ["Offer a human.".to_owned(), "Slow down.".to_owned()]);
    let _ = p.process_window(&window()).unwrap();
    assert_eq!(p.actuator().directives(), &["Offer a human.".to_owned()]);
    let _ = p.expire_before(1_000, 1_300).unwrap();
    assert!(p.actuator().directives().is_empty());
    for base in BASES {
        assert_eq!(compose(base, p.actuator().directives()), base);
    }
}

#[test]
fn controlled_case_protected_prune_is_not_a_directive() {
    let windows = vec![
        vec![reading(Axis::Arousal, 0.95, 400)],
        vec![reading(Axis::Arousal, 0.95, 800)],
    ];
    let rule = Rule::new(
        "prune_protected",
        Axis::Arousal,
        0.3,
        0.5,
        vec![Action::Prune(ToolId::from("escalate"))],
    );
    let mut p = pipeline(windows, vec![rule]);
    for _ in 0..2 {
        let _ = p.process_window(&window()).unwrap();
    }
    assert!(p.actuator().directives().is_empty());
    assert_eq!(compose(BASES[0], p.actuator().directives()), BASES[0]);
}

fn arb_text() -> impl Strategy<Value = String> {
    prop_oneof![
        Just("Confirm before acting.".to_owned()),
        Just("Slow down.".to_owned()),
        Just(BEGIN.to_owned()),
        "[a-zA-Z ,.ñ«»\n]{0,24}",
    ]
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 512, .. ProptestConfig::default() })]

    #[test]
    fn prop_exposed_directives_are_exactly_the_active_rule_texts(
        base in "(?s).{0,80}",
        texts in prop::collection::vec(arb_text(), 1..4),
        values in prop::collection::vec((0.0f32..1.0, 0.0f32..1.0, any::<bool>()), 1..30),
        expire_at in prop::option::of(0usize..30),
    ) {
        let rules: Vec<Rule> = texts
            .iter()
            .enumerate()
            .map(|(i, t)| directive_rule(["r0", "r1", "r2", "r3"][i], if i % 2 == 0 { Axis::Arousal } else { Axis::Valence }, t))
            .collect();
        let windows: Vec<Vec<SignalReading>> = values
            .iter()
            .enumerate()
            .map(|(k, &(v, c, valence))| {
                vec![SignalReading {
                    value: v,
                    axis: if valence { Axis::Valence } else { Axis::Arousal },
                    confidence: c,
                    timestamp_ms: 400 * (k as u64 + 1),
                }]
            })
            .collect();
        let mut p = pipeline(windows, rules);
        for k in 0..values.len() {
            let _ = p.process_window(&window()).unwrap();
            if expire_at == Some(k) {
                let now = 400 * (k as u64 + 1);
                let _ = p.expire_before(now + 1, now + 1).unwrap();
            }
            let exposed = p.actuator().directives().to_vec();
            for d in &exposed {
                prop_assert!(texts.contains(d), "directive not taken verbatim from a rule");
            }
            prop_assert_eq!(&exposed, &expected_directives(&p));
            let composed = compose(&base, &exposed);
            prop_assert!(composed.as_bytes().starts_with(base.as_bytes()));
            if exposed.is_empty() {
                prop_assert_eq!(composed.as_bytes(), base.as_bytes());
            }
        }
    }
}
