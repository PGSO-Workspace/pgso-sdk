//! Bounded contract exploration, not a population or interaction-utility study.
//!
//! Enumerate every trace of length 0..=5 over seven observation events for
//! H=1,2,3. The reference is a small specification implementation, not PGSO
//! output. It tests a fixed baseline, one axis and catalog effects only.
//! No acoustic model, host expiry, adaptive baseline or callback is exercised.

use pgso_core::{
    Action, AudioWindow, Axis, Catalog, DecisionEngine, Direction, EngineConfig, Pgso, Rule,
    RuleEngine, RuleSet, Signal, SignalReading, Tool, ToolId,
};
use std::collections::{HashSet, VecDeque};

struct Replay(VecDeque<Vec<SignalReading>>);

impl Signal for Replay {
    fn extract(&mut self, _window: &AudioWindow) -> Vec<SignalReading> {
        self.0.pop_front().unwrap_or_default()
    }
}

#[derive(Default)]
struct Reference {
    last_time: Option<u64>,
    run: u32,
    side: Option<bool>,
    restricted: bool,
}

impl Reference {
    fn observe(&mut self, reading: Option<&SignalReading>, h: u32) {
        let Some(r) = reading else {
            return;
        };
        if !r.value.is_finite()
            || !(0.0..=1.0).contains(&r.confidence)
            || r.confidence < 0.5
            || self.last_time.is_some_and(|last| r.timestamp_ms < last)
        {
            return;
        }
        self.last_time = Some(r.timestamp_ms);
        if (r.value - 0.5).abs() < 0.25 {
            self.run = 0;
            self.side = None;
            self.restricted = false;
            return;
        }
        let rising = r.value > 0.5;
        if self.side != Some(rising) {
            self.run = 0;
        }
        self.side = Some(rising);
        self.run += 1;
        if self.run >= h {
            self.restricted = rising;
        }
    }
}

fn batch(symbol: u8, index: usize) -> Vec<SignalReading> {
    if symbol == 6 {
        return vec![];
    }
    let (value, confidence) = match symbol {
        0 => (0.5, 0.9),
        1 | 5 => (0.875, 0.9),
        2 => (0.125, 0.9),
        3 => (0.875, 0.1),
        4 => (f32::NAN, 0.9),
        _ => unreachable!(),
    };
    vec![SignalReading {
        value,
        axis: Axis::Arousal,
        confidence,
        timestamp_ms: if symbol == 5 {
            0
        } else {
            u64::try_from(index).unwrap() * 400
        },
    }]
}

fn check_trace(trace: &[u8], h: u32) -> usize {
    let input: Vec<Vec<SignalReading>> = trace
        .iter()
        .enumerate()
        .map(|(i, symbol)| batch(*symbol, i))
        .collect();
    let protected = HashSet::from([ToolId::from("escalate")]);
    let catalog = Catalog::new(vec![
        Tool::new("quote", "Quote"),
        Tool::new("escalate", "Escalate"),
    ]);
    let rule = Rule::new(
        "rising",
        Axis::Arousal,
        0.25,
        0.5,
        vec![
            Action::Prune(ToolId::from("quote")),
            Action::Prune(ToolId::from("escalate")),
        ],
    )
    .with_direction(Direction::Rising);
    let engine = DecisionEngine::try_new(EngineConfig {
        confidence_threshold: 0.5,
        deviation_threshold: 0.25,
        hysteresis_window: h,
        ema_alpha: 0.0,
        warmup_readings: 0,
        population_prior: 0.5,
    })
    .unwrap();
    let mut sdk = Pgso::builder()
        .signal(Replay(input.clone().into()))
        .engine(engine)
        .rules(RuleEngine::new(RuleSet::new(vec![rule]), protected.clone()))
        .actuator(pgso_actuator_local::LocalActuator::new(catalog, protected))
        .build()
        .unwrap();
    let mut reference = Reference::default();
    for (i, observations) in input.iter().enumerate() {
        reference.observe(observations.first(), h);
        let actual = sdk
            .process_window(&AudioWindow {
                samples: vec![],
                sample_rate: 16000,
                timestamp_ms: u64::try_from(i).unwrap() * 400,
            })
            .unwrap();
        assert_eq!(
            actual.contains(&ToolId::from("quote")),
            !reference.restricted,
            "trace={trace:?}; H={h}; step={i}"
        );
        let safe_tool = actual.find(&ToolId::from("escalate")).unwrap();
        assert_eq!(
            safe_tool.requires_step_up, reference.restricted,
            "protected tool mismatch: trace={trace:?}; H={h}; step={i}"
        );
    }
    trace.len()
}

fn visit(trace: &mut Vec<u8>, h: u32, counts: &mut (usize, usize)) {
    counts.0 += 1;
    counts.1 += check_trace(trace, h);
    if trace.len() == 5 {
        return;
    }
    for symbol in 0..7 {
        trace.push(symbol);
        visit(trace, h, counts);
        trace.pop();
    }
}

#[test]
fn exhaustive_fixed_baseline_58824_traces_284319_steps() {
    let mut counts = (0, 0);
    for h in 1..=3 {
        visit(&mut vec![], h, &mut counts);
    }
    assert_eq!(counts, (58_824, 284_319));
}
