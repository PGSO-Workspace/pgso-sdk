//! Golden trace of decisions, served policy and baseline trajectory for the
//! reproducibility scenarios. Regenerate with `PGSO_BLESS=1` only when a
//! governed change is intended and approved.
use pgso_actuator_local::LocalActuator;
use pgso_core::{
    Action, Actuator, AudioWindow, Axis, Catalog, DecisionEngine, Direction, EngineConfig,
    GovernanceState, Pgso, ReadingResult, Rule, RuleEngine, RuleSet, Signal, SignalReading, Tool,
    ToolId,
};
use std::cell::RefCell;
use std::collections::HashSet;
use std::fmt::Write as _;
use std::path::Path;
use std::rc::Rc;

type Slot = Rc<RefCell<Option<SignalReading>>>;

struct Scripted(Slot);
impl Signal for Scripted {
    fn extract(&mut self, _: &AudioWindow) -> Vec<SignalReading> {
        self.0.borrow_mut().take().into_iter().collect()
    }
}

fn configs() -> [(&'static str, EngineConfig); 2] {
    [
        (
            "contract",
            EngineConfig {
                confidence_threshold: 0.5,
                deviation_threshold: 0.3,
                hysteresis_window: 3,
                ema_alpha: 0.1,
                warmup_readings: 5,
                population_prior: 0.5,
            },
        ),
        (
            "telephone",
            EngineConfig {
                confidence_threshold: 0.35,
                deviation_threshold: 0.15,
                hysteresis_window: 3,
                ema_alpha: 0.05,
                warmup_readings: 5,
                population_prior: 0.44,
            },
        ),
    ]
}

fn engine(config: &EngineConfig) -> DecisionEngine {
    DecisionEngine::try_new(config.clone())
        .unwrap()
        .with_max_gap_ms(1200)
        .unwrap()
}

fn pipeline(config: &EngineConfig, slot: &Slot) -> Pgso<Scripted, LocalActuator> {
    let protected = HashSet::from([ToolId::from("human")]);
    Pgso::builder()
        .signal(Scripted(Rc::clone(slot)))
        .engine(engine(config))
        .rules(RuleEngine::new(
            RuleSet::new(vec![Rule::new(
                "elevated",
                Axis::Arousal,
                config.deviation_threshold,
                config.confidence_threshold,
                vec![
                    Action::Prune(ToolId::from("quote")),
                    Action::Prune(ToolId::from("human")),
                    Action::InjectDirective("Clarify.".into()),
                ],
            )
            .with_direction(Direction::Rising)]),
            protected.clone(),
        ))
        .actuator(LocalActuator::new(
            Catalog::new(vec![
                Tool::new("quote", "Quote"),
                Tool::new("human", "Human"),
            ]),
            protected,
        ))
        .build()
        .unwrap()
}

/// Reads the frozen reference through the public API: outlying probes on a
/// clone never adapt the baseline and report it once hysteresis is met.
fn reference(engine: &DecisionEngine) -> f32 {
    let mut probe = engine.clone();
    let far = SignalReading {
        value: 1.0e9,
        axis: Axis::Arousal,
        confidence: 1.0,
        timestamp_ms: u64::MAX,
    };
    (0..8)
        .find_map(|_| probe.process(&far))
        .expect("probe triggers")
        .baseline
}

fn served(state: &GovernanceState) -> String {
    let tools: Vec<String> = state
        .catalog
        .tools()
        .iter()
        .map(|t| format!("{}{}", t.id, if t.requires_step_up { "!" } else { "" }))
        .collect();
    format!("[{}] {:?}", tools.join(","), state.directives)
}

fn readings(scenario: &serde_json::Value) -> Vec<(u64, Option<(f32, f32)>)> {
    let mut segments = scenario["segments"].as_array().unwrap().clone();
    if let Some(ramp) = scenario.get("ramp") {
        let (from, to) = (ramp["from"].as_f64().unwrap(), ramp["to"].as_f64().unwrap());
        let count = ramp["count"].as_u64().unwrap();
        for i in 0..count {
            let value = from + (to - from) * i as f64 / (count - 1) as f64;
            segments.push(serde_json::json!([1, value, 0.9, null, 400]));
        }
    }
    if let Some(tail) = scenario.get("tail") {
        segments.extend(tail.as_array().unwrap().iter().cloned());
    }
    let mut out = Vec::new();
    let mut timestamp = 0;
    for segment in &segments {
        for _ in 0..segment[0].as_u64().unwrap() {
            timestamp += segment[4].as_u64().unwrap();
            let pair = segment[1]
                .as_f64()
                .map(|v| (v as f32, segment[2].as_f64().unwrap() as f32));
            out.push((timestamp, pair));
        }
    }
    out
}

fn trace() -> String {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let spec: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(
            root.join("experiments/reproducibility/governance-scenarios.json"),
        )
        .unwrap(),
    )
    .unwrap();
    let window = AudioWindow {
        samples: Vec::new(),
        sample_rate: 16_000,
        timestamp_ms: 0,
    };
    let mut out = String::new();
    for (label, config) in configs() {
        for scenario in spec["scenarios"].as_array().unwrap() {
            let name = scenario["name"].as_str().unwrap();
            let slot = Slot::default();
            let mut pgso = pipeline(&config, &slot);
            let mut alone = engine(&config);
            for (step, (timestamp_ms, pair)) in readings(scenario).into_iter().enumerate() {
                write!(out, "{label}/{name}#{step} t={timestamp_ms}").unwrap();
                let mut expected = None;
                if let Some((value, confidence)) = pair {
                    let reading = SignalReading {
                        value,
                        axis: Axis::Arousal,
                        confidence,
                        timestamp_ms,
                    };
                    let before = reference(&alone);
                    let output = alone.process(&reading);
                    expected = Some((
                        before.to_bits(),
                        if confidence < config.confidence_threshold {
                            ReadingResult::Abstained
                        } else if (value - before).abs() < config.deviation_threshold {
                            ReadingResult::Nominal
                        } else if output.is_some() {
                            ReadingResult::Triggered
                        } else {
                            ReadingResult::Pending
                        },
                    ));
                    let decision = output.map_or_else(
                        || "-".to_string(),
                        |o| format!("{:08x}/{:08x}", o.deviation.to_bits(), o.baseline.to_bits()),
                    );
                    write!(
                        out,
                        " in={:08x}/{:08x} ref={:08x} out={decision}",
                        value.to_bits(),
                        confidence.to_bits(),
                        before.to_bits(),
                    )
                    .unwrap();
                    *slot.borrow_mut() = Some(reading);
                } else {
                    out.push_str(" silence");
                }
                let _ = pgso.process_window(&window).unwrap();
                let logged: Vec<_> = pgso
                    .last_readings()
                    .iter()
                    .map(|r| (r.reference.to_bits(), r.result))
                    .collect();
                assert_eq!(logged, Vec::from_iter(expected), "{label}/{name}#{step}");
                writeln!(out, " served={}", served(&pgso.actuator().current_state())).unwrap();
            }
            writeln!(
                out,
                "{label}/{name} final_ref={:08x} entries={}",
                reference(&alone).to_bits(),
                pgso.audit_log().entries().len()
            )
            .unwrap();
            for transition in pgso.audit_log().transitions() {
                writeln!(out, "{label}/{name} {transition:?}").unwrap();
            }
        }
    }
    out
}

#[test]
fn scenario_outputs_match_golden() {
    let actual = trace();
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/golden/output_identity.txt");
    if std::env::var_os("PGSO_BLESS").is_some() {
        std::fs::write(&path, &actual).unwrap();
    }
    let golden = std::fs::read_to_string(&path).unwrap();
    for (line, (want, got)) in golden.lines().zip(actual.lines()).enumerate() {
        assert_eq!(want, got, "first difference at golden line {}", line + 1);
    }
    assert_eq!(golden.lines().count(), actual.lines().count());
}
