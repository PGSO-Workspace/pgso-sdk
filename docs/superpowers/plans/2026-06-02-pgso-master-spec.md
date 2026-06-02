# PGSO — Master Specification

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the PGSO (Paralinguistic Governance for State Orchestration) Rust SDK — an external governance layer that perceives the paralinguistic channel of speech and deterministically governs which tools an agent's catalog exposes.

**Architecture:** Pure deterministic core behind two extension traits (`Signal`, `Actuator`). Perception is probabilistic (signal + confidence); action is deterministic (verifiable code). The core has zero ML/HTTP/IO dependencies.

**Tech Stack:** Rust 2021, thiserror, proptest (dev), ort (signal crate only), serde/serde_json (MCP crate only)

---

## 1. Scope boundary

PGSO is an **external** governance layer that perceives the **paralinguistic channel** of speech (pitch, energy, jitter, rhythm) directly from raw audio **without ASR**, and uses it to **deterministically govern** which tools an agent's catalog exposes.

PGSO is NOT:
- a model, an orchestrator, or middleware living *inside* the agent
- an emotion detector
- a system that rewrites prompts or blocks tools punitively

Uncertainty lives in perception; the guarantee lives in the action.

---

## 2. Architecture invariants

**INV-1 — Pure core.** `pgso-core` MUST NOT depend on any ML runtime, HTTP client, audio library, or filesystem I/O. Any such dependency lives in a separate crate behind a feature flag.

**INV-2 — Two extension traits.** The core exposes exactly two boundaries: `Signal` (perception source) and `Actuator` (where governance is applied). Concrete extractors and transports are implementations, never part of the core.

**INV-3 — Determinism.** Given identical `SignalReading` inputs and identical rules, the `DecisionEngine` MUST produce identical `ScopeDecision` outputs. No wall-clock, no RNG, no ambient state in the decision path.

**INV-4 — Core never reaches outward.** The core receives `SignalReading`s and emits `ScopeDecision`s. It never calls a model or opens a socket.

**INV-5 — Auditability.** Every `ScopeDecision` carries an `AuditRecord` capturing the inputs that produced it (signal value, axis, threshold crossed, rule id, caller-supplied timestamp).

---

## 3. Safety guarantees

**G1 — Base prompt is sovereign.** PGSO MUST NOT rewrite the agent's system prompt. It MAY only append a demarcated, removable directive block, removed when governance returns to nominal.

**G2 — Inviolable allowlist.** Protected tools are NEVER pruned or degraded, for ANY signal sequence. Verified by property test.

**G3 — Non-punitive by default.** Default reaction escalates friction (`RequireStepUp`), not removal. `Prune` only for explicitly-prunable tools. A `Prune` targeting a protected tool is downgraded to `RequireStepUp`.

**G4 — Abstention on low confidence.** Below the confidence threshold, hold current state, emit no mutation.

**G5 — Full auditability.** Every intervention is traceable and reversible.

**Governing principle:** PGSO fails toward inaction, not intervention.

---

## 4. Core types — Rust definitions

These types live in `pgso-core`. They are the shared vocabulary across all milestones and all sub-agents. The code below is the contract.

### 4.1 Types (`crates/pgso-core/src/types.rs`)

```rust
/// Opaque identifier for a tool in the agent's catalog.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ToolId(String);

impl ToolId {
    pub fn as_str(&self) -> &str { &self.0 }
}

impl std::fmt::Display for ToolId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl From<&str> for ToolId {
    fn from(s: &str) -> Self { Self(s.to_string()) }
}

impl From<String> for ToolId {
    fn from(s: String) -> Self { Self(s) }
}

/// A tool in the agent's catalog.
#[derive(Debug, Clone, PartialEq)]
pub struct Tool {
    pub id: ToolId,
    pub name: String,
    pub requires_step_up: bool,
}

impl Tool {
    pub fn new(id: impl Into<ToolId>, name: impl Into<String>) -> Self {
        Self { id: id.into(), name: name.into(), requires_step_up: false }
    }
}

/// An ordered collection of tools exposed to the agent.
#[derive(Debug, Clone, PartialEq)]
pub struct Catalog {
    tools: Vec<Tool>,
}

impl Catalog {
    pub fn new(tools: Vec<Tool>) -> Self { Self { tools } }
    pub fn tools(&self) -> &[Tool] { &self.tools }
    pub fn len(&self) -> usize { self.tools.len() }
    pub fn is_empty(&self) -> bool { self.tools.is_empty() }
    pub fn contains(&self, id: &ToolId) -> bool { self.tools.iter().any(|t| t.id == *id) }
    pub fn find(&self, id: &ToolId) -> Option<&Tool> { self.tools.iter().find(|t| t.id == *id) }

    pub fn remove(&mut self, id: &ToolId) { self.tools.retain(|t| t.id != *id); }
    pub fn set_step_up(&mut self, id: &ToolId, flag: bool) {
        if let Some(t) = self.tools.iter_mut().find(|t| t.id == *id) {
            t.requires_step_up = flag;
        }
    }
    pub fn reset_all_step_ups(&mut self) {
        for t in &mut self.tools { t.requires_step_up = false; }
    }
}

/// The paralinguistic axis being measured.
/// Dominance is excluded in v1 (Phase 0 discarded it).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Axis {
    Valence,
    Arousal,
}

/// A single reading from a Signal extractor.
/// `value` is the raw per-channel model output, normalized to ~[0,1].
/// The DecisionEngine computes deviation relative to speaker baseline.
#[derive(Debug, Clone)]
pub struct SignalReading {
    pub value: f32,
    pub axis: Axis,
    pub confidence: f32,
    pub timestamp_ms: u64,
}

/// A window of raw audio samples for signal extraction.
#[derive(Debug, Clone)]
pub struct AudioWindow {
    pub samples: Vec<f32>,   // mono, resampled to model rate
    pub sample_rate: u32,
    pub timestamp_ms: u64,   // caller-supplied, NOT from wall-clock
}
```

### 4.2 Actions and audit (`crates/pgso-core/src/action.rs`)

```rust
use crate::types::{Axis, ToolId};

/// A governance action emitted by the rule engine.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub enum Action {
    /// No governance needed; when applied, restores catalog to nominal.
    Allow,
    /// Keep the tool but require confirmation before use.
    RequireStepUp(ToolId),
    /// Remove the tool from the served catalog (never for protected tools).
    Prune(ToolId),
    /// Append a demarcated directive block to the agent's context.
    InjectDirective(String),
}

/// Audit trail for a governance decision (INV-5).
#[derive(Debug, Clone)]
pub struct AuditRecord {
    pub timestamp_ms: u64,
    pub signal_value: Option<f32>,
    pub axis: Option<Axis>,
    pub deviation: Option<f32>,
    pub threshold_crossed: Option<f32>,
    pub rule_id: Option<String>,
}

impl AuditRecord {
    /// Empty audit record for hand-constructed decisions in tests.
    pub fn empty() -> Self {
        Self {
            timestamp_ms: 0, signal_value: None, axis: None,
            deviation: None, threshold_crossed: None, rule_id: None,
        }
    }
}

/// A governance decision: an action paired with its audit trail.
#[derive(Debug, Clone)]
pub struct ScopeDecision {
    pub action: Action,
    pub audit: AuditRecord,
}

impl ScopeDecision {
    /// Decision with an empty audit record (for M1 tests).
    pub fn new(action: Action) -> Self {
        Self { action, audit: AuditRecord::empty() }
    }

    pub fn with_audit(action: Action, audit: AuditRecord) -> Self {
        Self { action, audit }
    }
}
```

### 4.3 Errors (`crates/pgso-core/src/error.rs`)

```rust
use crate::types::ToolId;

#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum ActuatorError {
    #[error("unknown tool id: {0}")]
    UnknownTool(ToolId),
    #[error("actuator error: {0}")]
    Internal(String),
}
```

### 4.4 Traits (`crates/pgso-core/src/traits.rs`)

```rust
use crate::{action::ScopeDecision, error::ActuatorError, types::{AudioWindow, Catalog, SignalReading}};

/// Perception source. Implementations are stateful (may maintain running statistics).
pub trait Signal {
    /// Extract readings from an audio window.
    /// Returns one reading per axis available; empty if silent/sub-VAD.
    fn extract(&mut self, window: &AudioWindow) -> Vec<SignalReading>;
}

/// Exposure point where governance decisions mutate the tool catalog.
pub trait Actuator {
    fn current_catalog(&self) -> Catalog;
    fn apply(&mut self, decision: &ScopeDecision) -> Result<Catalog, ActuatorError>;
}
```

### 4.5 Engine output (`crates/pgso-core/src/engine.rs`, M2)

```rust
/// Output of the DecisionEngine when a sustained deviation is detected.
/// None means abstain (G4) or not sustained (hysteresis).
#[derive(Debug, Clone)]
pub struct EngineOutput {
    pub axis: Axis,
    pub raw_value: f32,
    pub deviation: f32,
    pub confidence: f32,
    pub baseline: f32,
    pub timestamp_ms: u64,
}
```

### 4.6 Standard test fixture (use across all milestones)

```rust
use std::collections::HashSet;

pub fn test_catalog() -> Catalog {
    Catalog::new(vec![
        Tool::new("search", "Web Search"),
        Tool::new("calculate", "Calculator"),
        Tool::new("close_sale", "Close Sale"),
        Tool::new("escalate", "Escalate to Human"),
    ])
}

pub fn test_protected() -> HashSet<ToolId> {
    HashSet::from([ToolId::from("escalate")])
}
```

---

## 5. Error handling

- All public APIs return `Result` with `thiserror`-derived error enums.
- No `.unwrap()` or `.expect()` in library code. Use `?` propagation.
- `.unwrap()` is acceptable in `#[cfg(test)]` code only.
- No panics in library code. The SDK must never crash the host process.

---

## 6. Crate layout and workspace

```
pgso-sdk/
├── Cargo.toml                          # workspace manifest
├── crates/
│   ├── pgso-core/                      # pure: traits, engine, rules, audit
│   │   ├── Cargo.toml                  # deps: thiserror; dev: proptest
│   │   └── src/
│   │       ├── lib.rs
│   │       ├── types.rs
│   │       ├── action.rs
│   │       ├── error.rs
│   │       ├── traits.rs
│   │       ├── engine.rs               # (M2)
│   │       ├── rules.rs                # (M2)
│   │       ├── audit.rs                # (M2)
│   │       └── pipeline.rs             # (M4)
│   ├── pgso-actuator-local/            # (M1)
│   │   ├── Cargo.toml                  # deps: pgso-core
│   │   └── src/lib.rs
│   ├── pgso-signal-egemaps/            # (M3) DEFAULT signal: pure-Rust DSP
│   │   ├── Cargo.toml                  # deps: pgso-core ONLY (no ML, no openSMILE)
│   │   └── src/
│   │       ├── lib.rs                  # EgemapsSignal + Signal impl
│   │       ├── windowing.rs            # sliding windows + frame framing
│   │       ├── dsp.rs                  # F0 (autocorr), RMS energy, jitter, shimmer, voicing
│   │       └── features.rs             # AcousticFeatures (auditability) + axis mapping
│   ├── pgso-signal-onnx/               # (opt-in, OUT OF SCOPE for M3) wav2vec2 via ort
│   │   ├── Cargo.toml                  # deps: pgso-core, ort — feature "onnx", ablation only
│   │   └── src/lib.rs                  # see m3-signal-onnx-backup.md
│   └── pgso-actuator-mcp/             # (M5)
│       ├── Cargo.toml                  # deps: pgso-core, serde, serde_json
│       └── src/lib.rs
```

### Workspace `Cargo.toml`

```toml
[workspace]
resolver = "2"
members = ["crates/*"]
```

---

## 7. Rust conventions

- Edition 2021
- `snake_case` for modules/functions, `UpperCamelCase` for types
- `cargo clippy -- -D warnings` must pass
- Prefer `&str` over `String` in function params; own `String` in struct fields
- `#[non_exhaustive]` on public enums
- `#[must_use]` on `Result`-returning methods
- Derive `Debug, Clone, PartialEq` on all public types; add `Eq, Hash` where semantically correct
- No unnecessary allocations; prefer borrowing

---

## 8. Global definition of done (every milestone)

1. `cargo build` succeeds for touched crates.
2. `cargo test` green, including named property tests.
3. `cargo clippy -- -D warnings` clean on touched crates.
4. Each acceptance criterion has at least one passing test.
5. `pgso-core` still has zero ML/HTTP/IO deps (INV-1).
6. STOP HERE boundary respected — no later-milestone work leaks in.

---

## 9. Milestone sequence

| # | Milestone | Proves | Spec |
|---|-----------|--------|------|
| 1 | Local actuator hides one tool by flag | exposure point is controllable | `m1-local-actuator.md` |
| 2 | Deterministic core | decision logic + allowlist invariant | `m2-deterministic-core.md` |
| 3 | Paralinguistic signal (eGeMAPS DSP, default) | real auditable signal behind the trait | `m3-signal-egemaps.md` |
| 4 | End-to-end wiring | prosody alters catalog (the PoC) | `m4-e2e-wiring.md` |
| 5 | MCP adapter + mock signal | transport agnosticism proven | `m5-transport-agnosticism.md` |

---

## 10. How to use these specs with Claude Code

For each milestone:
1. Provide this master spec + the single milestone spec.
2. Instruct: "Implement only this milestone. Write the tests first, then the code to pass them. Stop at the boundary."
3. Verify against Section 8 before moving on.

Acceptance criteria use EARS form (WHEN/WHERE/IF... THE SYSTEM SHALL...). Each becomes a test.

---

## 11. Sub-agent orchestration

### 11.1 Dependency graph

```
M1 (core types + LocalActuator)
 ▼
M2 (DecisionEngine + RuleEngine + allowlist invariant)
 ▼
M3 (Signal: eGeMAPS DSP extractor, default — pure Rust, auditable)
 ▼
M4 (end-to-end wiring = PoC)
 ▼
M5 (MCP adapter + mock signal = agnosticism)
```

**Strictly sequential:** M1 → M2 → M3 → M4. Never parallelize across these.

**Safe to parallelize:** within a milestone (e.g. M2: property tests + engine against same spec); independent crates after dependency met (M5 MCP vs future HTTP adapter).

### 11.2 Rules

**R1** One sub-agent per milestone task. Give it master spec + its milestone spec.

**R2** Do not start milestone N+1 until N is DONE per Section 8.

**R3** Sub-agents write tests first, then code.

**R4** STOP HERE boundaries are enforced. Crossing one is a rejected result.

**R5** After each milestone, verify Global DoD + milestone DoD.

**R6** `pgso-core` changes only in M1 and M2 (types, traits, engine, rules). M4 adds the pipeline builder (generic, no concrete deps). M3 and M5 MUST NOT modify `pgso-core`.

**R7** `prop_allowlist_never_pruned` (M2) is the single most important test. Its absence or failure is a blocking defect.

**R8** Before coding M5, verify the current MCP `tools/list` / `notifications/tools/list_changed` shape against the live spec. If it differs, adjust the adapter design first.

**R9** M3 is eGeMAPS-first: the DEFAULT signal is an owned pure-Rust DSP extractor (`pgso-signal-egemaps`, interpretable LLD subset), NOT openSMILE (proprietary, non-commercial) and NOT the ONNX model (1.2GB, opt-in only). A sub-agent that makes ONNX or openSMILE the default in M3, or binds to openSMILE at all, has violated the spec → reject. ONNX stays behind feature `onnx` in a separate crate for later ablation, out of scope for the M3 build.
