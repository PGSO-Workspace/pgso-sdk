# Known Issues / Cleanup Backlog

## context.md is stale: describes an architecture the shipped SDK does not implement

**Type:** docs-only cleanup (no code change implied)
**Status:** open

### Summary

`context.md` is broadly out of date. Beyond the project-name and punitive-framing
fixes already made on `bench/decision-latency`, sections §2–§4 describe a stack,
decision formula, and state machine that the shipped `pgso-core` /
`pgso-signal-egemaps` crates **do not implement**. If the paper (or onboarding)
cites `context.md` as an architecture reference, it will mislead. Nothing about
the actual engine behavior is wrong — only `context.md`'s description of it.

### Discrepancies (claim → shipped reality)

**§2 Stack**
- "Async Runtime: `tokio`" → no `tokio` in any crate. `pgso-core` depends only on
  `thiserror`; there are 0 async tests.
- "ML Edge Inference (Audio): `ort` (ONNX Runtime)" → `pgso-signal-egemaps` is
  **pure-Rust DSP, no ML runtime**. `ort`/ONNX (wav2vec2 ablation) is a *planned
  separate crate* `pgso-signal-onnx` (see the comment in
  `crates/pgso-signal-egemaps/Cargo.toml`), not present.
- "JSON Manipulation: `serde`, `serde_json`" → `serde_json` is used only by
  `pgso-actuator-mcp`; the core has no serde dependency.
- "Builder Pattern (`PgsoBuilder`)" → accurate.

**§3 "Math Engine (Discrepancy)"**
- Claims `D = |R_text - R_voice| * (1 + Sigma_jitter)`.
- Shipped (`crates/pgso-core/src/engine.rs`): `deviation = |value - baseline|` on a
  **single axis**, where `baseline` is a three-layer estimate (population prior →
  cumulative-mean warm-up → EMA), gated by **hysteresis** (N consecutive
  above-threshold windows) and **confidence abstention** (G4). There is no
  `R_text`/`R_voice` product and no jitter multiplier in the decision path.

**§4 "Finite State Machine (FSM) Rules" (S0–S5)**
- Described as a 6-state FSM (Nominal / Mild Incongruence / Stress Detected /
  Panic / Affective Containment / Fallback) with actions like "inject warning
  tags", "prune high-latency tools", "expose ONLY emotional support safe-tools",
  "return empty tools array".
- Shipped: there is **no S0–S5 `State` enum** in any crate (prose only). The actual
  `Action` enum (`crates/pgso-core/src/action.rs`) is
  `{ Allow, RequireStepUp, Prune, InjectDirective }`, applied by a rule engine over
  the continuous deviation/hysteresis/abstention output. Governance is
  **non-punitive** (RequireStepUp by default; Prune only for unprotected tools; G2
  inviolable allowlist), which contradicts the punitive S3/S4
  "panic/containment/empty-array" model.

### Already fixed on `bench/decision-latency`
- Title: "Prosody-Gated State Orchestrator" → "Paralinguistic Governance for State
  Orchestration".
- §1: removed the retired "prevent unsafe executions during acute user stress"
  framing.

### Recommendation
Either:
1. **Rewrite §2–§4** to match the shipped SDK (thiserror-only core, pure-DSP
   eGeMAPS signal, deviation/EMA/hysteresis/abstention engine, the real `Action`
   enum, non-punitive governance), **or**
2. **Mark `context.md` as a historical/aspirational design note**, superseded by
   `docs/superpowers/plans/2026-06-02-pgso-master-spec.md` and `docs/prd-v12.md`,
   and stop citing it as the architecture reference.

The paper should cite the master spec / implementation report, not `context.md`.
