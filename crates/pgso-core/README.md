# pgso-core

The pure, deterministic core of **PGSO** — *Paralinguistic Governance for State
Orchestration*, an external layer that governs which tools an agent's catalog
exposes based on **how** the interlocutor sounds.

This crate is the engine. It has **no ML, HTTP, or I/O dependencies** (only
`thiserror`, INV-1) and never reaches outward: it consumes `SignalReading`s and
emits `ScopeDecision`s. Perception and action plug in behind two traits, so the
core never changes when you swap an extractor or a transport.

## What's inside

- **`Signal`** (perception extension point) and **`Actuator`** (action extension
  point) — the two symmetric boundaries. Implement these to add an extractor or a
  transport; the core is untouched. Their trait docs spell out the implementer
  contract.
- **`DecisionEngine`** — a three-layer speaker baseline (population prior →
  warm-up mean → EMA), **hysteresis** (a lone spike never fires), and
  **abstention** below a confidence threshold.
- **`RuleEngine` + `pgso_rules!`** — compile-time rules that map a *sustained*
  deviation to `ScopeDecision`s, and enforce the **inviolable allowlist**: a
  `Prune` of a protected tool is downgraded to `RequireStepUp`, never dropped.
- **`AuditLog` / `AuditRecord`** — every intervention *and* reversal is traceable.
- **`Pgso<S, A>`** — the generic pipeline that wires a `Signal` to an `Actuator`.

## Where it sits

```
 perception (probabilistic)        deterministic · pure        action (verifiable)
   impl Signal  ───SignalReading──▶   pgso-core   ───ScopeDecision──▶  impl Actuator
 (pgso-signal-egemaps)            (DecisionEngine, RuleEngine,      (pgso-actuator-local,
                                   AuditLog, Pgso<S,A>)              pgso-actuator-mcp)
```

## Safety guarantees enforced here

**G1** prompt-sovereign (only a removable directive block is appended) · **G2**
inviolable allowlist (RuleEngine downgrade) · **G3** non-punitive default · **G4**
abstention on low confidence · **G5** full auditability. The governing principle:
*PGSO fails toward inaction, not intervention.*

## Minimal usage

`pgso-core` is generic over a `Signal` and an `Actuator`; pair it with the default
extractor and the reference actuator:

```rust
use std::collections::HashSet;
use pgso_core::{pgso_rules, Action, DecisionEngine, EngineConfig, Pgso, RuleEngine, Catalog, Tool, ToolId};
use pgso_signal_egemaps::EgemapsSignal;
use pgso_actuator_local::LocalActuator;

let protected = HashSet::from([ToolId::from("escalate")]);
let rules = pgso_rules! {
    rule "tense_arousal" {
        axis: Arousal, deviation: 0.3, confidence: 0.5,
        action: Action::Prune(ToolId::from("close_sale"))
    }
};
let catalog = Catalog::new(vec![Tool::new("close_sale", "Close Sale"), Tool::new("escalate", "Escalate")]);

let mut pgso = Pgso::builder()
    .signal(EgemapsSignal::new(16_000))
    .engine(DecisionEngine::new(EngineConfig { confidence_threshold: 0.5, deviation_threshold: 0.3,
        hysteresis_window: 3, ema_alpha: 0.1, warmup_readings: 5, population_prior: 0.5 }))
    .rules(RuleEngine::new(rules, protected.clone()))
    .actuator(LocalActuator::new(catalog, protected))
    .build()?;

let served = pgso.process_window(&window)?; // the catalog the agent may see this turn
```

See the [workspace README](../../README.md) for architecture, guarantees, and the
end-to-end example.
