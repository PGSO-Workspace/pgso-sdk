# Milestone 1 — Local Actuator: Hide One Tool by Flag

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that PGSO can control which tools an agent's catalog exposes, through the `Actuator` trait, with a minimal owned implementation. NO signal, NO decision engine, NO network.

**Architecture:** Define core types in `pgso-core`, implement `LocalActuator` in `pgso-actuator-local`. Hand-built `ScopeDecision`s test the actuator directly.

**Tech Stack:** Rust 2021, thiserror

---

## File structure

```
pgso-sdk/
├── Cargo.toml                              # workspace (CREATE)
├── crates/
│   ├── pgso-core/
│   │   ├── Cargo.toml                      # (CREATE)
│   │   └── src/
│   │       ├── lib.rs                      # (CREATE) re-exports
│   │       ├── types.rs                    # (CREATE) ToolId, Tool, Catalog, Axis, SignalReading, AudioWindow
│   │       ├── action.rs                   # (CREATE) Action, AuditRecord, ScopeDecision
│   │       ├── error.rs                    # (CREATE) ActuatorError
│   │       └── traits.rs                   # (CREATE) Signal, Actuator
│   └── pgso-actuator-local/
│       ├── Cargo.toml                      # (CREATE)
│       └── src/
│           └── lib.rs                      # (CREATE) LocalActuator + tests
```

---

## Requirements (EARS form)

**REQ-1.1** THE `pgso-core` crate SHALL define an `Actuator` trait with `current_catalog(&self) -> Catalog` and `apply(&mut self, decision: &ScopeDecision) -> Result<Catalog, ActuatorError>`.

**REQ-1.2** THE `pgso-core` crate SHALL define `Catalog` (ordered `Vec<Tool>`), `Tool { id: ToolId, name: String, requires_step_up: bool }`, and `ToolId` (newtype over `String`).

**REQ-1.3** THE `pgso-core` crate SHALL define `ScopeDecision { action: Action, audit: AuditRecord }` with `Action` enum: `Allow`, `RequireStepUp(ToolId)`, `Prune(ToolId)`, `InjectDirective(String)`.

**REQ-1.4** THE `pgso-actuator-local` crate SHALL provide `LocalActuator` implementing `Actuator`, holding an in-memory catalog and a set of protected tool ids.

**REQ-1.5** WHEN `apply` receives `Prune(id)` AND `id` is NOT protected, THE `LocalActuator` SHALL return a catalog without that tool.

**REQ-1.6** WHEN `apply` receives `Prune(id)` AND `id` IS protected, THE `LocalActuator` SHALL return the catalog unchanged, no error (G2 embryo).

**REQ-1.7** WHEN `apply` receives `Allow`, THE `LocalActuator` SHALL restore the full base catalog (all tools, all step-ups cleared, all directives removed).

**REQ-1.8** WHEN `apply` receives `RequireStepUp(id)`, THE `LocalActuator` SHALL keep the tool and set `requires_step_up = true`.

**REQ-1.9** WHEN `apply` receives `InjectDirective(text)`, THE `LocalActuator` SHALL store the directive and return the catalog unchanged.

**REQ-1.10** THE `LocalActuator` SHALL NOT perform network, filesystem, or model calls (INV-4).

**REQ-1.11** Applying `Allow` SHALL preserve tool order (catalogs are ordered).

---

## Tasks

### Task 1: Set up workspace and crate skeletons

**Files:** Create: `Cargo.toml`, `crates/pgso-core/Cargo.toml`, `crates/pgso-core/src/lib.rs`

- [ ] **Step 1: Create workspace Cargo.toml**

```toml
[workspace]
resolver = "2"
members = ["crates/*"]
```

- [ ] **Step 2: Create pgso-core Cargo.toml**

```toml
[package]
name = "pgso-core"
version = "0.1.0"
edition = "2021"

[dependencies]
thiserror = "2"
```

- [ ] **Step 3: Create pgso-core/src/lib.rs (empty re-exports)**

```rust
pub mod types;
pub mod action;
pub mod error;
pub mod traits;

pub use types::*;
pub use action::*;
pub use error::*;
pub use traits::*;
```

- [ ] **Step 4: Verify**

Run: `cargo build -p pgso-core`
Expected: compiles (modules are empty stubs)

---

### Task 2: Core types, actions, errors, and traits

**Files:** Create: `crates/pgso-core/src/types.rs`, `action.rs`, `error.rs`, `traits.rs`

- [ ] **Step 1: Write types.rs**

Copy EXACTLY from master spec Section 4.1. All types: `ToolId`, `Tool`, `Catalog`, `Axis`, `SignalReading`, `AudioWindow`.

- [ ] **Step 2: Write action.rs**

Copy EXACTLY from master spec Section 4.2. Types: `Action`, `AuditRecord`, `ScopeDecision`.

- [ ] **Step 3: Write error.rs**

Copy EXACTLY from master spec Section 4.3. Type: `ActuatorError`.

- [ ] **Step 4: Write traits.rs**

Copy EXACTLY from master spec Section 4.4. Traits: `Signal`, `Actuator`.

- [ ] **Step 5: Verify**

Run: `cargo build -p pgso-core`
Expected: clean compile, zero warnings

- [ ] **Step 6: Run clippy**

Run: `cargo clippy -p pgso-core -- -D warnings`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add crates/pgso-core/ Cargo.toml
git commit -m "feat(core): define shared types, traits, and errors for pgso-core"
```

---

### Task 3: Write failing tests for LocalActuator

**Files:** Create: `crates/pgso-actuator-local/Cargo.toml`, `crates/pgso-actuator-local/src/lib.rs`

- [ ] **Step 1: Create pgso-actuator-local Cargo.toml**

```toml
[package]
name = "pgso-actuator-local"
version = "0.1.0"
edition = "2021"

[dependencies]
pgso-core = { path = "../pgso-core" }
```

- [ ] **Step 2: Write the test module (tests first, TDD)**

Write all 6 tests in `crates/pgso-actuator-local/src/lib.rs`:

```rust
use std::collections::HashSet;
use pgso_core::{
    Actuator, Action, ActuatorError, AuditRecord, Catalog, ScopeDecision, Tool, ToolId,
};

// LocalActuator struct and impl will go here (Task 4)

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> (LocalActuator, ToolId, ToolId, ToolId, ToolId) {
        let tools = vec![
            Tool::new("search", "Web Search"),
            Tool::new("calculate", "Calculator"),
            Tool::new("close_sale", "Close Sale"),
            Tool::new("escalate", "Escalate to Human"),
        ];
        let protected = HashSet::from([ToolId::from("escalate")]);
        let actuator = LocalActuator::new(Catalog::new(tools), protected);
        let search = ToolId::from("search");
        let close_sale = ToolId::from("close_sale");
        let escalate = ToolId::from("escalate");
        let calculate = ToolId::from("calculate");
        (actuator, search, close_sale, escalate, calculate)
    }

    #[test]
    fn test_prune_removes_unprotected_tool() {
        let (mut act, _, close_sale, _, _) = fixture();
        let decision = ScopeDecision::new(Action::Prune(close_sale.clone()));
        let catalog = act.apply(&decision).unwrap();
        assert_eq!(catalog.len(), 3);
        assert!(!catalog.contains(&close_sale));
    }

    #[test]
    fn test_prune_protected_tool_is_noop() {
        let (mut act, _, _, escalate, _) = fixture();
        let original = act.current_catalog();
        let decision = ScopeDecision::new(Action::Prune(escalate.clone()));
        let catalog = act.apply(&decision).unwrap();
        assert!(catalog.contains(&escalate));
        assert_eq!(catalog.len(), original.len());
    }

    #[test]
    fn test_allow_returns_full_catalog() {
        let (mut act, _, close_sale, _, _) = fixture();
        // First prune a tool
        act.apply(&ScopeDecision::new(Action::Prune(close_sale.clone()))).unwrap();
        assert!(!act.current_catalog().contains(&close_sale));
        // Then Allow to restore
        let catalog = act.apply(&ScopeDecision::new(Action::Allow)).unwrap();
        assert_eq!(catalog.len(), 4);
        assert!(catalog.contains(&close_sale));
    }

    #[test]
    fn test_stepup_keeps_tool_flagged() {
        let (mut act, search, _, _, _) = fixture();
        let decision = ScopeDecision::new(Action::RequireStepUp(search.clone()));
        let catalog = act.apply(&decision).unwrap();
        assert!(catalog.contains(&search));
        let tool = catalog.find(&search).unwrap();
        assert!(tool.requires_step_up);
    }

    #[test]
    fn test_catalog_order_stable() {
        let (mut act, _, _, _, _) = fixture();
        let original = act.current_catalog();
        let catalog = act.apply(&ScopeDecision::new(Action::Allow)).unwrap();
        let orig_ids: Vec<_> = original.tools().iter().map(|t| &t.id).collect();
        let new_ids: Vec<_> = catalog.tools().iter().map(|t| &t.id).collect();
        assert_eq!(orig_ids, new_ids);
    }

    #[test]
    fn test_inject_directive_stores_text() {
        let (mut act, _, _, _, _) = fixture();
        let text = "Tension detected. Switch to clarification.".to_string();
        let decision = ScopeDecision::new(Action::InjectDirective(text.clone()));
        let catalog = act.apply(&decision).unwrap();
        // Catalog unchanged
        assert_eq!(catalog.len(), 4);
        // Directive stored
        assert_eq!(act.directives(), &[text]);
    }
}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cargo test -p pgso-actuator-local`
Expected: FAIL — `LocalActuator` not defined yet

---

### Task 4: Implement LocalActuator

**Files:** Modify: `crates/pgso-actuator-local/src/lib.rs`

- [ ] **Step 1: Implement LocalActuator**

Add above the test module:

```rust
use std::collections::HashSet;
use pgso_core::{
    Action, Actuator, ActuatorError, Catalog, ScopeDecision, ToolId,
};

pub struct LocalActuator {
    base_catalog: Catalog,
    active_catalog: Catalog,
    protected: HashSet<ToolId>,
    directive_blocks: Vec<String>,
}

impl LocalActuator {
    pub fn new(catalog: Catalog, protected: HashSet<ToolId>) -> Self {
        Self {
            active_catalog: catalog.clone(),
            base_catalog: catalog,
            protected,
            directive_blocks: Vec::new(),
        }
    }

    pub fn directives(&self) -> &[String] {
        &self.directive_blocks
    }
}

impl Actuator for LocalActuator {
    fn current_catalog(&self) -> Catalog {
        self.active_catalog.clone()
    }

    fn apply(&mut self, decision: &ScopeDecision) -> Result<Catalog, ActuatorError> {
        match &decision.action {
            Action::Allow => {
                // Restore to nominal (G1: remove directives too)
                self.active_catalog = self.base_catalog.clone();
                self.directive_blocks.clear();
            }
            Action::Prune(id) => {
                if !self.protected.contains(id) {
                    // G2: protected tools are never pruned
                    self.active_catalog.remove(id);
                }
            }
            Action::RequireStepUp(id) => {
                self.active_catalog.set_step_up(id, true);
            }
            Action::InjectDirective(text) => {
                self.directive_blocks.push(text.clone());
            }
        }
        Ok(self.active_catalog.clone())
    }
}
```

- [ ] **Step 2: Run tests**

Run: `cargo test -p pgso-actuator-local`
Expected: all 6 tests PASS

- [ ] **Step 3: Run clippy**

Run: `cargo clippy -p pgso-actuator-local -- -D warnings`
Expected: clean

- [ ] **Step 4: Verify INV-1 — pgso-core has no ML/HTTP/IO deps**

Run: `cargo metadata --no-deps -p pgso-core --format-version 1 | grep -o '"dependencies":\[[^]]*\]'`
Expected: only `thiserror`

- [ ] **Step 5: Commit**

```bash
git add crates/pgso-actuator-local/
git commit -m "feat(actuator-local): implement LocalActuator with G2 allowlist protection"
```

---

## Definition of done

- [x] `pgso-core` defines: `Actuator`, `Catalog`, `Tool`, `ToolId`, `ScopeDecision`, `Action`, `AuditRecord`, `ActuatorError`, `Signal`, `SignalReading`, `AudioWindow`, `Axis`
- [x] `pgso-actuator-local` provides `LocalActuator` implementing `Actuator`
- [x] All 6 acceptance tests pass
- [x] `pgso-core` has zero ML/HTTP/IO deps (INV-1)
- [x] `cargo clippy -- -D warnings` clean on both crates

---

## STOP HERE

Do NOT:
- implement `Signal` or any audio handling
- implement `DecisionEngine`, baseline, or hysteresis
- write the `pgso_rules!` macro
- add MCP or HTTP

`ScopeDecision`s in tests are hand-built via `ScopeDecision::new(action)`. The point: can a hand-built decision mutate the catalog correctly through the trait? If yes, the spine exists.
