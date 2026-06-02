# PGSO Rust SDK — Implementation Report

**Date:** 2026-06-02
**Branch:** `feat/pgso-sdk-implementation` → merged to `main` (`--no-ff`, commit `2a55249`; local merge, not pushed)
**Method:** Spec-driven, subagent-driven development (one implementer per milestone + two-stage review)
**Outcome:** All 5 milestones complete. 63 tests / 13 suites green. `cargo clippy -- -D warnings` clean. Release build clean. Final review: **READY TO MERGE**.

---

## 1. Executive summary

PGSO (Paralinguistic Governance for State Orchestration) is an external governance layer that perceives the **paralinguistic channel** of speech (pitch, energy, jitter, rhythm) directly from raw audio **without ASR**, and uses it to **deterministically govern** which tools an agent's catalog exposes. Perception is probabilistic (a signal + confidence); the action is deterministic (verifiable code). The uncertainty lives in perception; the guarantee lives in the action.

The build delivers the complete proof-of-concept: a paralinguistic signal deterministically governing an agent's tool catalog, with five safety guarantees verified, and the deterministic core demonstrably **agnostic** to both the transport (local + MCP) and the signal source (prosody + a mock latency signal) — proven by an *empty `pgso-core` diff*, not asserted.

---

## 2. What was delivered

| Crate | Responsibility | src LOC* | Normal deps |
|---|---|---|---|
| `pgso-core` | Pure deterministic core: types, `Signal`/`Actuator` traits, `DecisionEngine`, `RuleEngine` + `pgso_rules!`, `AuditLog`, generic `Pgso<S,A>` pipeline | 1130 | **`thiserror` only** |
| `pgso-actuator-local` | Reference actuator; G2 allowlist backstop | 159 | `pgso-core` |
| `pgso-signal-egemaps` | **Default** signal: pure-Rust DSP LLD extractor (F0, energy, jitter, shimmer, voicing), auditable | 652 | `pgso-core` |
| `pgso-actuator-mcp` | MCP transport adapter (`tools/list` + `list_changed`) | 345 | `pgso-core`, `serde_json` |

*src LOC includes `#[cfg(test)]` unit-test modules co-located in `lib.rs`.

**Footprint:** 36 files changed, +8,616 lines (code + the 6 planning specs). 25 commits.

---

## 3. Process

Execution followed `superpowers:subagent-driven-development`: a fresh implementer subagent per milestone, given the master spec + exactly one milestone spec, writing tests first (TDD), then code. Each milestone passed through:

1. **Implementer subagent** → implements, self-reviews, commits.
2. **Orchestrator verification** → independent `cargo test` / `clippy` / invariant checks + source read.
3. **Spec-compliance reviewer subagent** → verifies the code matches the spec by reading it (does not trust the report); confirms nothing missing/extra/out-of-scope.
4. **Code-quality reviewer subagent** → assesses correctness, idiom, structure, test quality.
5. **Fixes** for any findings → **independent re-review** of the fix → mark complete.

The dependency chain M1 → M2 → M3 → M4 → M5 was enforced strictly (each milestone began only after its upstream was DONE).

The reviews did real work — every milestone but the first surfaced a substantive issue that was fixed before proceeding (Section 5).

---

## 4. Milestone-by-milestone

### M1 — Local actuator (the exposure point)
Built `pgso-core`'s shared vocabulary (`ToolId`, `Tool`, `Catalog`, `Axis`, `SignalReading`, `AudioWindow`, `Action`, `AuditRecord`, `ScopeDecision`, `ActuatorError`, `Signal`, `Actuator`) and `LocalActuator`. Proved a hand-built `ScopeDecision` mutates the served catalog through the `Actuator` trait, with the G2 allowlist already in embryo (pruning a protected tool is a no-op).
**Review fixes:** documented the actuator as deliberately infallible/idempotent (vs. the dead `UnknownTool` path); removed dead `Catalog::reset_all_step_ups`; added a `debug_assert` tripwire on the `#[non_exhaustive] Action` wildcard arm.

### M2 — Deterministic core (the thesis asset)
Built `DecisionEngine` (three-layer baseline: population prior → cumulative-mean warm-up → EMA; hysteresis; abstention), `RuleEngine` + the `pgso_rules!` macro (compile-time rule declaration), and `AuditLog`. The headline property test `prop_allowlist_never_pruned` proves G2 for **any** random signal sequence and **any** random rule set.
**Review fix (important):** the warm-up formula made the *first* reading's baseline equal its own value → deviation structurally 0 and `population_prior` dead. Switched to **measure-then-update** (measure deviation against the established baseline before folding the reading in), making the prior a genuine cold-start reference. Also: `debug_assert` config validation, saturating hysteresis counter, single-pass `evaluate`, and a non-vacuous EMA-adaptation test. Independently re-reviewed with traced arithmetic confirming M4 trigger timing was preserved.

### M3 — Paralinguistic signal (eGeMAPS-first, DSP, default)
Pivoted (mid-plan, at the user's direction) from an ONNX-first design to an **owned pure-Rust DSP extractor** as the default: interpretable eGeMAPS low-level descriptors (F0 via autocorrelation, RMS energy, jitter, shimmer, voicing), windowing + VAD + per-channel standardization, and an `extract_explained` API exposing the `AcousticFeatures` behind each reading (auditability). No openSMILE (license), no `ort`, no 1.2 GB model. ONNX remains opt-in/out-of-scope for a later ablation (backup spec preserved).
**Review fix (important):** voicing confidence `best_r/r0` was pitch-biased (low-pitched voices reported lower confidence for equal signal quality — a fairness issue, since confidence feeds abstention). Switched to a proper **NCCF** (normalized by the overlapped head/tail energy) at the selected lag, keeping the energy-biased lag *selection* for octave-down protection. Independently re-reviewed (Cauchy-Schwarz bound, region-consistency, lag-independence).

### M4 — End-to-end wiring (the PoC)
Built the generic `Pgso<S: Signal, A: Actuator>` builder + `process_window`, wiring Signal → DecisionEngine → RuleEngine → Actuator. Demonstrated: sustained altered prosody → target tool pruned → calm → catalog restored + directive removed — deterministic, audited. The builder returns `Result<_, PgsoBuildError>` (no panic).
**Review fix (important, REQ-4.6):** the level-triggered recovery `Allow` (a real catalog reversal) was unrecorded, violating "every catalog mutation shall be recorded" + G5's traceable-and-reversible. Now recorded — but only when the catalog *actually* changes (no-op calm-window Allows are suppressed). The recovery test asserts the reversal appears in the audit trail.

### M5 — Transport agnosticism (proven, not claimed)
Built `pgso-actuator-mcp` (`McpActuator`) producing MCP `tools/list` payloads + a `notifications/tools/list_changed` change flag, and a `MockLatencySignal`. The M4 scenario re-runs identically over MCP by swapping only the actuator; a non-prosodic signal drives the unchanged pipeline. **R8 honored:** the MCP shape was verified against the live 2025-06-18 spec before coding. The headline result: `git diff <pre-M5> -- crates/pgso-core` is **empty**.
**Review fix:** MCP `name`/`title` were inverted vs. spec (a client calls tools by `name`); fixed to `name`=`ToolId`, `title`=display label. Dropped an unused `serde` dep; MSRV-agnostic test cleanup.

---

## 5. Architecture invariants & safety guarantees (final state)

| | Holds? | Where enforced / proven |
|---|---|---|
| **INV-1** Pure core | ✅ | `cargo tree -p pgso-core -e normal` = `thiserror` only; ML/DSP/serde confined to leaf crates; enforced by `dep_hygiene` tests |
| **INV-2** Two trait boundaries | ✅ | `traits.rs` exposes exactly `Signal` + `Actuator`; leaves never depend on each other in normal deps |
| **INV-3** Determinism | ✅ | No clock/RNG/ambient state in the decision path; `prop_determinism` + `test_e2e_determinism_on_trace` |
| **INV-4** Core never reaches outward | ✅ | No net/fs/io/socket refs anywhere in `pgso-core/src` |
| **INV-5** Auditability | ✅ | Every `ScopeDecision` carries an `AuditRecord`; pipeline records interventions **and** restores (`AuditRecord::restore`) |
| **G1** Base prompt sovereign | ✅ | Actuators only append/clear a demarcated directive block; `Allow` clears it on restore |
| **G2** Inviolable allowlist | ✅ | **Defense-in-depth ×3:** RuleEngine downgrade + LocalActuator guard + McpActuator guard. `prop_allowlist_never_pruned` (the headline test, proven non-vacuous) |
| **G3** Non-punitive default | ✅ | `Prune(protected)` → `RequireStepUp` (never dropped); `test_prune_protected_downgraded_to_stepup` |
| **G4** Abstain on low confidence | ✅ | Engine early-returns below threshold without adapting baseline; `prop_low_confidence_abstains`; egemaps abstains on silence |
| **G5** Full auditability | ✅ | Append-only `AuditLog`; restores recorded only on real change |

**Governing principle — "fail toward inaction" — upheld:** abstention on low confidence, hysteresis against isolated spikes, protected-tool downgrade, and an idempotent/tolerant actuator that never errors on a structurally-valid decision.

---

## 6. Test inventory (63 tests, 13 suites)

| Suite | Count | Highlights |
|---|---|---|
| `pgso-core` unit | 13 | engine (abstain, isolated-spike, sustained-trigger, baseline-adapt, determinism, cold-start), rules (match, downgrade, default-stepup, audit), audit |
| `pgso-core` `tests/property_tests.rs` | 4 | **`prop_allowlist_never_pruned`**, `prop_determinism`, `prop_isolated_spike_no_trigger`, `prop_low_confidence_abstains` |
| `pgso-core` `tests/e2e.rs` | 6 | sustained-prune, recovery-restore, protected-survives, audit-trail, determinism-on-trace, calm-smoke |
| `pgso-core` doc-test | 1 | `pgso_rules!` macro usability |
| `pgso-actuator-local` unit | 6 | prune/protected-noop/allow/stepup/order/directive |
| `pgso-signal-egemaps` unit | 19 | DSP (F0 on known tones, RMS, voicing), windowing, functionals, axis mapping, reading shape, VAD-skips-silence, explainability |
| `pgso-signal-egemaps` `tests/dep_hygiene.rs` | 3 | no openSMILE/`ort`/ML dep; core stays audio/ML-free; substring false-positive guard |
| `pgso-actuator-mcp` unit | 9 | object-safety, prune-omits, protected-survives, allow-restores, change-flag (set/ack/no-op cases), payload shape, stepup |
| `pgso-actuator-mcp` `tests/e2e_mcp.rs` | 1 | M4 scenario re-run over MCP |
| `pgso-actuator-mcp` `tests/second_signal.rs` | 1 | mock latency signal accepted, zero core change |

**Benchmark (REQ-3.7):** eGeMAPS window→reading p50 ≈ 10.1–10.3 ms for a 1 s/16 kHz window (criterion) — comfortably real-time on CPU, no model.

---

## 7. Thesis claims — demonstrated, not asserted

- **Transport agnosticism:** the same `pgso-core` runs over `LocalActuator` and `McpActuator`; the M5 diff to `crates/pgso-core` is **empty** (verified). The adapter swap is the only changed line in the e2e scenario.
- **Signal agnosticism:** `MockLatencySignal` (a non-prosodic source on `Axis::Arousal`) drives the unchanged pipeline with zero core change.
- **Auditable governance:** the default signal is interpretable DSP — each reading carries its F0/energy/jitter/shimmer-vs-baseline rationale (`extract_explained`), so a governance reaction is explainable.

---

## 8. Commit history (feature work)

```
2a55249 Merge feat/pgso-sdk-implementation: PGSO SDK (M1–M5)
df950e1 chore(actuator-mcp): drop unused direct serde dependency (final review)
2c73540 fix(actuator-mcp): correct MCP name/title mapping + hygiene (M5 review)
162da3a feat(actuator-mcp): M5 complete — transport agnosticism demonstrated, not claimed
f997826 feat(actuator-mcp): implement McpActuator with MCP tools/list payload generation
83b9936 refactor(core): name restore marker as const + AuditRecord::restore() (M4 review minors)
a3597e2 fix(core): record catalog-restoring Allow in audit log (M4 review, REQ-4.6)
5ef9054 feat(core): Pgso pipeline — end-to-end governance PoC with all guarantees verified
95216f6 refactor(signal-egemaps): address M3 review — pitch-independent voicing, API hygiene
6719c08 feat(signal-egemaps): dep-hygiene tests, latency bench, default-signal example
3627ca7 feat(signal-egemaps): EgemapsSignal Signal impl with explainable readings (REQ-3.6)
394039e feat(signal-egemaps): windowing, functionals, documented Arousal axis mapping, running baseline
5068961 feat(signal-egemaps): pure-Rust DSP primitives — RMS energy, autocorrelation F0, voicing
3d6c6aa chore: update Cargo.lock for proptest dev-dependency tree
6563f58 refactor(core): address M2 review — measure-then-update, config asserts, single-pass
5f2931d feat(core): complete M2 — engine, rules, audit, all property tests green
1dad867 test(core): add property tests — prop_allowlist_never_pruned is the headline
1e8c969 feat(core): implement RuleEngine, pgso_rules! macro, and G2 allowlist enforcement
e05a378 feat(core): implement DecisionEngine with baseline EMA, hysteresis, abstention
d19be43 refactor(m1): address code review — document infallible/idempotent actuator
c948d7b chore: track Cargo.lock for reproducible workspace builds
c241e07 feat(actuator-local): implement LocalActuator with G2 allowlist protection
7f8024a feat(core): define shared types, traits, and errors for pgso-core
8a4d968 docs(plans): pivot M3 to eGeMAPS-first (pure-Rust DSP default signal)
2c17601 docs(plans): add PGSO master spec and 5 milestone implementation plans
```

---

## 9. Known limitations & recommended follow-ups (non-blocking)

1. **Synthetic validation only.** The whole system is verified on mock signals and synthetic tones. The eGeMAPS→Arousal mapping constants and the threshold/hysteresis tuning are deliberate, documented design choices — not yet validated on real speech. **M6 (domain validation on AI-seller audio)** is the natural next milestone before any production claim.
2. **Actuator duplication.** `LocalActuator::apply` and `McpActuator::apply` share ~40 lines of governance state-machine logic. This is *intentional* for now (the independent copies are a G2 backstop and each is tested) — but extract a shared `GovernedCatalog` into `pgso-core` during a future core-touching milestone to remove drift risk (the M5 name/title bug is exactly the divergence a shared type prevents). Doing it now would break the M5 empty-diff proof.
3. **ONNX ablation crate** (`pgso-signal-onnx`, feature `onnx`) is intentionally absent; build it later, behind the feature flag, for a DSP-vs-neural ablation (both implement the same `Signal` trait — a clean comparison).
4. **Signal robustness:** `estimate_f0` has a documented, modest octave-error margin (fine for clean speech; revisit with a YIN-style normalized difference if real audio surfaces octave errors).
5. **Pre-release polish:** crate-level READMEs, `#![deny(missing_docs)]` on `pgso-core`, and a declared workspace MSRV.

---

## 10. Build & run

```bash
cargo test --workspace                              # 63 tests, 13 suites
cargo clippy --workspace --all-targets -- -D warnings
cargo build --workspace --release
cargo run -p pgso-signal-egemaps --example extract  # default-signal demo (no model)
cargo bench -p pgso-signal-egemaps                  # latency p50/p95/p99
```

Governing documents: `docs/superpowers/plans/2026-06-02-pgso-master-spec.md` (constitution) + the five milestone specs alongside it. The retired `context.md` (6-state FSM / discrepancy-formula design) is superseded by the master spec.
