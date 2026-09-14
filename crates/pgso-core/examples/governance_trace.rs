//! Deterministic synthetic trace for comparing fresh processes/builds.
//! This is a reproducibility check, not evidence of perception or human utility.
use pgso_actuator_local::LocalActuator;
use pgso_core::*;
use std::collections::{HashSet, VecDeque};
struct Readings(VecDeque<SignalReading>);
impl Signal for Readings {
    fn extract(&mut self, _: &AudioWindow) -> Vec<SignalReading> {
        self.0.pop_front().into_iter().collect()
    }
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let values = [0.5, 0.5, 0.95, 0.95, 0.95, 0.95, 0.5, 0.05, 0.05, 0.05];
    let readings = (0..512)
        .map(|i| SignalReading {
            value: values[i % values.len()],
            axis: Axis::Arousal,
            confidence: if i % 17 == 0 { 0.1 } else { 0.9 },
            timestamp_ms: i as u64 * 400,
        })
        .collect();
    let protected = HashSet::from([ToolId::from("human")]);
    let mut p = Pgso::builder()
        .signal(Readings(readings))
        .engine(
            DecisionEngine::try_new(EngineConfig {
                confidence_threshold: 0.5,
                deviation_threshold: 0.3,
                hysteresis_window: 3,
                ema_alpha: 0.1,
                warmup_readings: 5,
                population_prior: 0.5,
            })?
            .with_max_gap_ms(1200)?,
        )
        .rules(RuleEngine::new(
            RuleSet::new(vec![Rule::new(
                "elevated",
                Axis::Arousal,
                0.3,
                0.5,
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
        .build()?;
    for i in 0..512 {
        let served = p.process_window(&AudioWindow {
            samples: vec![],
            sample_rate: 16000,
            timestamp_ms: i * 400,
        })?;
        println!(
            "{}|{:?}|{:?}|{}",
            i,
            served,
            p.actuator().directives(),
            p.audit_log().transitions().len()
        );
    }
    for t in p.audit_log().transitions() {
        println!(
            "transition|{}|{:?}|{:?}|{:?}",
            t.timestamp_ms, t.reading, t.before, t.after
        );
    }
    Ok(())
}
