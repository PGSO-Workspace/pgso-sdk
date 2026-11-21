//! OE4-I3 / G1 on the MCP actuator: the exposed directive blocks are verbatim rule texts of active
//! contributions, they never enter the `tools/list` payload, and they are fully retired, so a host
//! that composes `base + block` serves the base prompt byte for byte when no directive is active.
//! Mirrors `pgso-core/tests/base_prompt_preservation.rs`, which runs the same cases on `LocalActuator`.

use pgso_actuator_mcp::McpActuator;
use pgso_core::*;
use proptest::prelude::*;
use std::collections::{HashSet, VecDeque};

fn compose(base: &str, directives: &[String]) -> String {
    if directives.is_empty() {
        return base.to_owned();
    }
    format!(
        "{base}\n\n<pgso-directives>\n{}\n</pgso-directives>",
        directives.join("\n")
    )
}

struct Replay(VecDeque<Vec<SignalReading>>);

impl Signal for Replay {
    fn extract(&mut self, _: &AudioWindow) -> Vec<SignalReading> {
        self.0.pop_front().unwrap_or_default()
    }
}

fn window() -> AudioWindow {
    AudioWindow {
        samples: vec![],
        sample_rate: 16_000,
        timestamp_ms: 0,
    }
}

const IDS: [&str; 4] = ["r0", "r1", "r2", "r3"];

/// Every string value (not key) in a JSON document.
fn strings(v: &serde_json::Value, out: &mut Vec<String>) {
    match v {
        serde_json::Value::String(s) => out.push(s.clone()),
        serde_json::Value::Array(a) => a.iter().for_each(|x| strings(x, out)),
        serde_json::Value::Object(o) => o.values().for_each(|x| strings(x, out)),
        _ => {}
    }
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 256, .. ProptestConfig::default() })]

    #[test]
    fn prop_mcp_directives_are_verbatim_retired_and_outside_tools_payload(
        base in "(?s).{0,80}",
        texts in prop::collection::vec("[A-Z][a-zA-Z ,.ñ]{3,24}", 1..4),
        values in prop::collection::vec((0.0f32..1.0, 0.0f32..1.0, any::<bool>()), 1..30),
    ) {
        let rules: Vec<Rule> = texts
            .iter()
            .enumerate()
            .map(|(i, t)| {
                Rule::new(
                    IDS[i],
                    if i % 2 == 0 { Axis::Arousal } else { Axis::Valence },
                    0.3,
                    0.5,
                    vec![Action::InjectDirective(t.clone())],
                )
            })
            .collect();
        let windows: VecDeque<Vec<SignalReading>> = values
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
        let protected = HashSet::from([ToolId::from("escalate")]);
        let mut p = Pgso::builder()
            .signal(Replay(windows))
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
            .rules(RuleEngine::new(RuleSet::new(rules), protected.clone()))
            .actuator(McpActuator::new(
                Catalog::new(vec![Tool::new("quote", "q"), Tool::new("escalate", "e")]),
                protected,
            ))
            .build()
            .unwrap();
        for _ in 0..values.len() {
            let _ = p.process_window(&window()).unwrap();
            let exposed = p.actuator().directives().to_vec();
            let mut expected: Vec<String> = Vec::new();
            if let Some(t) = p.audit_log().transitions().last() {
                for d in &t.active {
                    if let Action::InjectDirective(text) = &d.action {
                        if !expected.contains(text) {
                            expected.push(text.clone());
                        }
                    }
                }
            }
            prop_assert_eq!(&exposed, &expected);
            let mut payload_strings = Vec::new();
            strings(&p.actuator().tools_list_response(), &mut payload_strings);
            for d in &exposed {
                prop_assert!(texts.contains(d));
                // Catalog strings legitimately appear in the payload; directive texts must not.
                let catalog_string = ["quote", "q", "escalate", "e"].contains(&d.as_str());
                prop_assert!(
                    catalog_string || !payload_strings.iter().any(|v| v == d),
                    "directive leaked into tools/list"
                );
            }
            let composed = compose(&base, &exposed);
            prop_assert!(composed.as_bytes().starts_with(base.as_bytes()));
            if exposed.is_empty() {
                prop_assert_eq!(composed.as_bytes(), base.as_bytes());
            }
        }
    }
}
