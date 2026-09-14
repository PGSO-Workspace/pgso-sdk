//! End-to-end governance demo: the DEFAULT eGeMAPS signal + `LocalActuator`.
//!
//! Synthesizes a conversation arc — calm baseline, a stretch of sustained vocal
//! tension, then calm again — and feeds it window-by-window through the full
//! PGSO pipeline (`Signal -> DecisionEngine -> RuleEngine -> Actuator`). It
//! prints the served tool catalog per window and the audit trail at the end.
//!
//! What you should see:
//! - **calm warm-up** → full catalog, no interventions;
//! - **sustained tension** → `close_sale` pruned (G3 rule), `escalate` untouched
//!   (G2 protected), a directive injected (G1);
//! - **return to calm** → catalog restored, directive dropped (G1) — and the
//!   whole arc recorded in the `AuditLog` (G5).
//!
//! No model download; CPU-only. Run: `cargo run -p pgso-signal-egemaps --example governance`.

// Synthetic-tone generation casts sample indices/rate between int and float; the
// precision loss is irrelevant to a demo fixture (mirrors the `extract` example).
#![allow(clippy::cast_precision_loss)]

use std::collections::HashSet;
use std::error::Error;
use std::f32::consts::PI;

use pgso_actuator_local::LocalActuator;
use pgso_core::{
    Action, AudioWindow, Catalog, DecisionEngine, EngineConfig, Pgso, RuleEngine, Tool, ToolId,
};
use pgso_signal_egemaps::EgemapsSignal;

const SR: u32 = 16_000;

/// One 0.8 s window. `amp` drives loudness (→ energy); `vibrato_depth` drives
/// pitch dynamism (→ F0 std). Calm = quiet + steady; tense = loud + wavering —
/// the two inputs the eGeMAPS arousal mapping responds to.
fn window(ts_ms: u64, amp: f32, f0: f32, vibrato_depth: f32) -> AudioWindow {
    let n = (0.8 * SR as f32) as usize;
    let mut samples = Vec::with_capacity(n);
    let mut phase = 0.0f32;
    for i in 0..n {
        let t = i as f32 / SR as f32;
        let inst = f0 + vibrato_depth * (2.0 * PI * 5.0 * t).sin(); // 5 Hz vibrato
        phase += 2.0 * PI * inst / SR as f32;
        samples.push(amp * phase.sin().mul_add(1.0, 0.3 * (2.0 * phase).sin()));
    }
    AudioWindow {
        samples,
        sample_rate: SR,
        timestamp_ms: ts_ms,
    }
}

fn calm(ts: u64) -> AudioWindow {
    window(ts, 0.05, 120.0, 0.0)
}

fn tense(ts: u64) -> AudioWindow {
    window(ts, 0.32, 230.0, 45.0)
}

fn ids(c: &Catalog) -> String {
    c.tools()
        .iter()
        .map(|t| t.id.as_str())
        .collect::<Vec<_>>()
        .join(", ")
}

fn main() -> Result<(), Box<dyn Error>> {
    let protected = HashSet::from([ToolId::from("escalate")]);

    // "On sustained vocal tension (arousal), stop pushing the close and clarify."
    let rules = pgso_core::RuleSet::new(vec![pgso_core::Rule::new(
        "elevated_arousal",
        pgso_core::Axis::Arousal,
        0.3,
        0.5,
        vec![
            Action::Prune(ToolId::from("close_sale")),
            Action::InjectDirective(
                "Ask whether clarification would help; avoid assuming an emotion.".into(),
            ),
        ],
    )
    .with_direction(pgso_core::Direction::Rising)]);

    let catalog = Catalog::new(vec![
        Tool::new("search", "Web Search"),
        Tool::new("close_sale", "Close Sale"), // prunable under sustained tension
        Tool::new("escalate", "Escalate to Human"), // protected — never pruned (G2)
    ]);

    let mut pgso = Pgso::builder()
        .signal(EgemapsSignal::new(SR))
        .engine(
            DecisionEngine::new(EngineConfig {
                confidence_threshold: 0.5,
                deviation_threshold: 0.3,
                hysteresis_window: 3, // a lone tense window never fires
                ema_alpha: 0.1,
                warmup_readings: 5,
                population_prior: 0.1, // reference for this synthetic quiet fixture only
            })
            .with_max_gap_ms(2000)?,
        )
        .rules(RuleEngine::new(rules, protected.clone()))
        .actuator(LocalActuator::new(catalog, protected))
        .build()?;

    // Conversation arc: 5 calm (warm-up) · 4 tense (sustained) · 5 calm (recovery).
    let arc = [(false, 5usize), (true, 4), (false, 5)];
    println!("win  phase  served catalog");
    let mut ts = 0u64;
    let mut step = 0u32;
    for (is_tense, count) in arc {
        for _ in 0..count {
            let w = if is_tense { tense(ts) } else { calm(ts) };
            let served = pgso.process_window(&w)?;
            let label = if is_tense { "tense" } else { "calm " };
            println!("{step:>3}  {label}  [{}]", ids(&served));
            ts += 800;
            step += 1;
        }
    }

    println!("\nfinal served catalog: [{}]", ids(&pgso.current_catalog()));
    let entries = pgso.audit_log().entries();
    println!("\naudit trail ({} entries):", entries.len());
    for d in entries {
        let rule = d.audit.rule_id.as_deref().unwrap_or("-");
        println!("  rule={rule:<16} action={:?}", d.action);
    }
    Ok(())
}
