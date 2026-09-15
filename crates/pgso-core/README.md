# pgso-core

The pure, deterministic core of **PGSO** — *Paralinguistic Governance for State
Orchestration*, an external layer that governs which tools an agent's catalog
exposes using numerical acoustic readings and caller-defined rules.

This crate is the engine. It has **no ML, HTTP, or I/O dependencies** (only
`thiserror`, INV-1) and never reaches outward: it consumes `SignalReading`s and
emits `ScopeDecision`s. Perception and action plug in through two traits. Implementations must satisfy
the contracts defined by those traits.

## What's inside

- **`Signal`** (perception extension point) and **`Actuator`** (action extension
  point) — extension boundaries for an extractor or policy adapter. Their trait
  documentation specifies the implementer contract.
- **`DecisionEngine`** — a three-layer speaker baseline (population prior →
  warm-up mean → EMA), **hysteresis** over same-side deviations, and
  **abstention** below a confidence threshold. Only nominal readings update the
  baseline; abstention holds existing policy rather than restoring permissions.
- **`RuleEngine` + `pgso_rules!`** — configured rules that map a *sustained*
  deviation to `ScopeDecision`s, and enforce the **inviolable allowlist**: a
  `Prune` of a protected tool is downgraded to `RequireStepUp`, never dropped.
- **`AuditLog` / `AuditRecord`** — records effective policy changes in memory;
  persistence and logging failed attempts are host responsibilities.
- **`Pgso<S, A>`** — the generic pipeline that wires a `Signal` to an `Actuator`.

## Where it sits

```
 signal extraction                  policy evaluation            catalog policy
   impl Signal  ───SignalReading──▶   pgso-core   ───ScopeDecision──▶  impl Actuator
 (pgso-signal-egemaps)            (DecisionEngine, RuleEngine,      (pgso-actuator-local,
                                   AuditLog, Pgso<S,A>)              pgso-actuator-mcp)
```

## Policy boundaries

Protected tools cannot be pruned by the reference governance state; attempted
prunes are downgraded to step-up. Unprotected tools can be pruned, so step-up is
not a universal default. Low-confidence or missing readings retain existing
restrictions. Nominal readings retire the relevant axis's contributions; expiry
is an explicit host policy. See the [governance contract](../../docs/governance-contract.md).

Catalog visibility and step-up metadata do not enforce authorization at dispatch.
The host must enforce execution permissions, for example through the
[HTTP runtime](../pgso-actuator-http/README.md). Restoring a catalog does not undo
previously executed effects. Tests establish implementation behavior under their
stated assumptions, not validity of an acoustic interpretation.

## Minimal usage

`pgso-core` is generic over a `Signal` and an `Actuator`. The
[workspace quick start](../../README.md#quick-start) provides a complete example
with validated `EngineConfig`, an explicit rising-direction rule, and an audio
window. `Rule::new` and `pgso_rules!` use `Direction::Either` unless a rule is
changed with `Rule::with_direction`.
