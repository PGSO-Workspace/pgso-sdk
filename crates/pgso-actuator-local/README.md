# pgso-actuator-local

The **reference `Actuator`** for [PGSO](../../README.md): a minimal, in-memory
tool catalog. It applies the deterministic core's `ScopeDecision`s
to the served catalog — pruning a tool, flagging a step-up, appending a removable
directive block — and enforces protected-tool retention within the catalog. Protected tools must
be present in the nominal catalog to be available.

## Where it sits

Implements `pgso_core::Actuator`; the default action target wired into
`pgso_core::Pgso`. The same trait is implemented by
[`pgso-actuator-mcp`](../pgso-actuator-mcp).

## Policy behavior

- **G1** — `Action::Allow` restores the nominal catalog and drops any appended
  directive blocks.
- **G2** — protected ids are never pruned (inviolable-allowlist backstop, even if
  a rule targets one directly).
- `Action::RequireStepUp` marks a tool as requiring confirmation. Unprotected
  tools may still be removed by `Action::Prune`.

This adapter manages catalog state, not tool execution. A host must enforce
permissions and confirmation requirements at dispatch; see the
[HTTP runtime](../pgso-actuator-http/README.md). Restoring catalog state cannot
undo an executed action.

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
