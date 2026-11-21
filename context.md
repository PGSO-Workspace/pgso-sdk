# PGSO-SDK (Paralinguistic Governance for State Orchestration) - Technical Context

This file describes the SDK as shipped (versions 0.1.0 and 0.2.0). It replaces the earlier design note that described a
discrepancy score and a six-state machine (S0-S5) that were never implemented. The normative references are the
governance contract (`docs/governance-contract.md`), the master spec (`docs/superpowers/plans/2026-06-02-pgso-master-spec.md`),
the PRD (`docs/prd-v12.md`) and the C4 model (`docs/pgso-c4.dsl`). When this file and the code disagree, the code wins.

## 1. Project Goal
A deterministic, auditable governance layer for the tool calling of voice agents. PGSO turns uncertain paralinguistic
readings, obtained without transcription, into bounded and reversible conditions on the tool catalog and on tool
invocation. Governance is non-punitive: the default reaction adds friction (require step-up) before removing a tool,
protected tools are never removed (G2), and the SDK never rewrites the host's base prompt (G1). PGSO does not judge the
user's emotional state and does not decide whether an intervention is warranted; it applies the host's policy.

## 2. Architecture & Stack
* Language: Rust (2021 edition, minimum 1.83). No async runtime in any crate.
* `pgso-core`: decision engine, rule engine, governance state, pipeline and audit log. Depends only on `thiserror`
  (no machine-learning runtime, no I/O).
* `pgso-signal-egemaps`: pure-Rust DSP extractor inspired by eGeMAPS (voiced-frame energy and F0 variability); no ONNX or
  other inference runtime. Implements the `Signal` trait; any other extractor can replace it through that trait.
* `pgso-actuator-local`, `pgso-actuator-mcp` (serves `tools/list`, uses `serde_json`) and `pgso-actuator-http` (execution
  runtime that checks host permissions, current policy, schemas and confirmations before each callback). They implement the
  `Actuator` trait.
* Construction: `Pgso::builder()` (signal, engine, rules, actuator), fallible `build()`; `DecisionEngine::try_new` validates
  the configuration in every build profile.

## 3. Decision Engine (per axis)
Each reading is `(value, axis, confidence, timestamp_ms)`. The engine:
* abstains (holds baseline, persistence counter and policy) when the value or confidence is not finite, when
  `confidence < confidence_threshold`, or when the timestamp is earlier than the last accepted one (G4);
* compares the value with a speaker-relative baseline: population prior, then cumulative mean during warm-up, then EMA
  `b_t = (1 - alpha) * b_{t-1} + alpha * x_t`; the baseline is frozen while the reading deviates;
* counts a reading as deviating when `|x_t - b_{t-1}| >= deviation_threshold`; the counter grows with same-side deviations
  and resets on a nominal reading or a change of side;
* triggers when the counter reaches `hysteresis_window`; a nominal accepted reading retires the axis' contributions;
* optionally resets the run when the gap between accepted readings exceeds `with_max_gap_ms`.
There is no `R_text`, no semantic risk input and no jitter multiplier in the decision path.

## 4. Governance State (replaces the retired S0-S5 machine)
Per axis the observable states are nominal, pending (deviating, below hysteresis) and restricted (triggered). There is no
`State` enum with S0-S5 and no "panic", "containment" or "empty tools" state.
* Rules map a triggered output to actions: `Allow`, `RequireStepUp(tool)`, `Prune(tool)`, `InjectDirective(text)`.
  A `Prune` that targets a protected tool is converted to `RequireStepUp` (G2).
* The served state (`GovernanceState`: catalog plus directive blocks) is recomposed from the nominal catalog and the active
  contributions of all axes; restoring is a recomposition, not an undo.
* Every effective change is recorded as a `PolicyTransition` with before/after state, surviving contributions and its
  `TransitionCause` (activation, replacement, nominal reading, no matching rule, host expiry) (G5). Abstentions and failed
  commits are not transitions.
* Silence never restores permissions by itself; the trusted host retires stale contributions with
  `expire_before(cutoff_ms, timestamp_ms)`.
* Directive blocks are kept apart from the base prompt and removed when their contributions retire (G1).

## 5. Coding Guidelines for this Session
* Follow strict Rust idiomatic naming (`snake_case` for modules/functions, `UpperCamelCase` for Structs/Enums).
* No panics in library code; return errors (`ConfigError`, `ActuatorError`, `PgsoBuildError`).
* Keep `pgso-core` free of machine-learning and I/O dependencies (INV-1).
* The SDK MUST remain LLM-agnostic: it receives readings and a tool catalog and returns a governed catalog, directive blocks
  and an audit trail; it never sees or edits the base prompt.
* Run `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D warnings` and `cargo test --workspace`.
