# pgso-actuator-local

The **reference `Actuator`** for [PGSO](../../README.md): a minimal, in-memory
tool catalog you fully own. It applies the deterministic core's `ScopeDecision`s
to the served catalog — pruning a tool, flagging a step-up, appending a removable
directive block — and is the **G2 backstop**: a protected tool is never removed,
whatever the decision.

## Where it sits

Implements `pgso_core::Actuator`; the default action target wired into
`pgso_core::Pgso`. Because the boundary is a trait, you depend on this owned,
auditable reference rather than a black box — and can swap in another transport
(e.g. [`pgso-actuator-mcp`](../pgso-actuator-mcp)) without changing the core.

## Guarantees it upholds

- **G1** — `Action::Allow` restores the nominal catalog and drops any appended
  directive blocks.
- **G2** — protected ids are never pruned (inviolable-allowlist backstop, even if
  a rule targets one directly).
- **G3** — escalates friction (`Action::RequireStepUp`) rather than removing a
  tool punitively.

## Usage

```rust
use std::collections::HashSet;
use pgso_core::{Catalog, Tool, ToolId};
use pgso_actuator_local::LocalActuator;

let protected = HashSet::from([ToolId::from("escalate")]);
let catalog = Catalog::new(vec![
    Tool::new("close_sale", "Close Sale"),
    Tool::new("escalate", "Escalate to Human"), // protected — never pruned
]);
let actuator = LocalActuator::new(catalog, protected);
// pass `actuator` to `Pgso::builder().actuator(..)`; see the workspace README.
```
