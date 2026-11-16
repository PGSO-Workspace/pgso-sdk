//! Golden trace of the extractor and the full audio pipeline on a synthetic
//! clip built without libm. Regenerate with `PGSO_BLESS=1` only when a governed
//! change is intended and approved.
#![allow(clippy::cast_precision_loss)]

use pgso_actuator_local::LocalActuator;
use pgso_core::{
    Action, Actuator, AudioWindow, Axis, Catalog, DecisionEngine, Direction, EngineConfig, Pgso,
    ReadingResult, Rule, RuleEngine, RuleSet, SignalReading, Tool, ToolId,
};
use pgso_signal_egemaps::EgemapsSignal;
use std::collections::HashSet;
use std::fmt::Write as _;
use std::path::Path;

/// 1.6 s triangle wave; `wobble` alternates the period every eight cycles.
fn window(sr: u32, timestamp_ms: u64, amp: f32, period: usize, wobble: usize) -> AudioWindow {
    let mut samples = Vec::new();
    let (mut pos, mut cycle, mut current) = (0, 0, period);
    for _ in 0..sr as usize * 8 / 5 {
        let x = (4 * pos) as f32 / current as f32;
        let triangle = if x < 1.0 {
            x
        } else if x < 3.0 {
            2.0 - x
        } else {
            x - 4.0
        };
        samples.push(amp * triangle);
        pos += 1;
        if pos == current {
            pos = 0;
            cycle += 1;
            current = period + if (cycle / 8) % 2 == 1 { wobble } else { 0 };
        }
    }
    AudioWindow {
        samples,
        sample_rate: sr,
        timestamp_ms,
    }
}

fn clip(sr: u32) -> Vec<AudioWindow> {
    let scale = |samples_at_16k: usize| samples_at_16k * sr as usize / 16_000;
    let calm = |ts| window(sr, ts, 0.05, scale(132), 0);
    let tense = |ts| window(sr, ts, 0.32, scale(72), scale(24));
    let faint = |ts| window(sr, ts, 0.002, scale(132), 0);
    let mut out = Vec::new();
    for (index, kind) in "ccctttfcctcc".chars().enumerate() {
        let ts = index as u64 * 1600;
        out.push(match kind {
            'c' => calm(ts),
            't' => tense(ts),
            _ => faint(ts),
        });
    }
    out
}

fn config() -> EngineConfig {
    EngineConfig {
        confidence_threshold: 0.5,
        deviation_threshold: 0.3,
        hysteresis_window: 3,
        ema_alpha: 0.1,
        warmup_readings: 5,
        population_prior: 0.1,
    }
}

fn engine() -> DecisionEngine {
    DecisionEngine::try_new(config())
        .unwrap()
        .with_max_gap_ms(2000)
        .unwrap()
}

fn pipeline(sr: u32) -> Pgso<EgemapsSignal, LocalActuator> {
    let protected = HashSet::from([ToolId::from("escalate")]);
    Pgso::builder()
        .signal(EgemapsSignal::new(sr))
        .engine(engine())
        .rules(RuleEngine::new(
            RuleSet::new(vec![Rule::new(
                "elevated_arousal",
                Axis::Arousal,
                0.3,
                0.5,
                vec![
                    Action::Prune(ToolId::from("close_sale")),
                    Action::RequireStepUp(ToolId::from("search")),
                    Action::InjectDirective("Clarify.".into()),
                ],
            )
            .with_direction(Direction::Rising)]),
            protected.clone(),
        ))
        .actuator(LocalActuator::new(
            Catalog::new(vec![
                Tool::new("search", "Search"),
                Tool::new("close_sale", "Close Sale"),
                Tool::new("escalate", "Escalate"),
            ]),
            protected,
        ))
        .build()
        .unwrap()
}

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

fn trace() -> String {
    let mut out = String::new();
    for sr in [16_000, 8_000] {
        let mut extractor = EgemapsSignal::new(sr);
        let mut alone = engine();
        let mut pgso = pipeline(sr);
        for (index, window) in clip(sr).iter().enumerate() {
            let mut expected = Vec::new();
            for explained in extractor.extract_explained(window) {
                let (r, f) = (&explained.reading, &explained.features);
                let before = reference(&alone);
                let output = alone.process(r);
                expected.push((r.value.to_bits(), before.to_bits(), output.is_some()));
                let decision = output.map_or_else(
                    || "-".to_string(),
                    |o| format!("{:08x}/{:08x}", o.deviation.to_bits(), o.baseline.to_bits()),
                );
                let features = [
                    f.mean_f0_hz,
                    f.f0_std_hz,
                    f.mean_energy,
                    f.jitter,
                    f.shimmer,
                    f.voiced_fraction,
                    f.mean_voicing,
                    f.energy_vs_baseline,
                    f.f0_vs_baseline,
                ]
                .map(|x| format!("{:08x}", x.to_bits()))
                .join(",");
                writeln!(
                    out,
                    "{sr}#{index} t={} in={:08x}/{:08x} features={features} ref={:08x} out={decision}",
                    r.timestamp_ms,
                    r.value.to_bits(),
                    r.confidence.to_bits(),
                    before.to_bits(),
                )
                .unwrap();
            }
            let _ = pgso.process_window(window).unwrap();
            let logged: Vec<_> = pgso
                .last_readings()
                .iter()
                .map(|r| {
                    (
                        r.reading.value.to_bits(),
                        r.reference.to_bits(),
                        r.result == ReadingResult::Triggered,
                    )
                })
                .collect();
            assert_eq!(logged, expected, "{sr}#{index}");
            writeln!(
                out,
                "{sr}#{index} served={:?}",
                pgso.actuator().current_state()
            )
            .unwrap();
        }
        writeln!(
            out,
            "{sr} final_ref={:08x} entries={}",
            reference(&alone).to_bits(),
            pgso.audit_log().entries().len()
        )
        .unwrap();
        for transition in pgso.audit_log().transitions() {
            writeln!(out, "{sr} {transition:?}").unwrap();
        }
    }
    out
}

#[test]
fn audio_outputs_match_golden() {
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
