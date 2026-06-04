# pgso-actuator-mcp

An `Actuator` for [PGSO](../../README.md) that serves the governed tool catalog
over the **Model Context Protocol** (MCP) `tools/list` transport, and tracks when
a `notifications/tools/list_changed` is owed to the client.

## Why it exists — the agnosticism proof

This crate is PGSO's **transport-agnosticism proof** (Milestone 5). It implements
the *exact same* `pgso_core::Actuator` boundary as
[`pgso-actuator-local`](../pgso-actuator-local), with identical governance
semantics (G1/G2/G3), and the deterministic core drives it **with a zero-line
diff to `pgso-core`** — agnosticism demonstrated, not asserted.

## What it does

- **`tools_list_response()`** → the MCP `tools/list` *result* object
  `{ "tools": [ { name, title, description, inputSchema } ] }`, verified against
  the 2025-06-18 MCP spec. Pruned tools are absent; protected tools a rule
  targeted are still present (**G2 over MCP**).
- **`has_changed()` / `acknowledge_change()`** → drive
  `notifications/tools/list_changed`, set **only** when the served catalog
  actually changes (a no-op prune, an already-nominal `Allow`, or an injected
  directive does not flip it).

The JSON-RPC envelope (`jsonrpc`/`id`/`result`) and pagination (`nextCursor`) are
the surrounding MCP server's responsibility, intentionally out of scope for the
governance layer.

## Usage

```rust
use std::collections::HashSet;
use pgso_core::{Action, Actuator, Catalog, ScopeDecision, Tool, ToolId};
use pgso_actuator_mcp::McpActuator;

let protected = HashSet::from([ToolId::from("escalate")]);
let catalog = Catalog::new(vec![Tool::new("close_sale", "Close Sale"), Tool::new("escalate", "Escalate")]);
let mut mcp = McpActuator::new(catalog, protected);

mcp.apply(&ScopeDecision::new(Action::Prune(ToolId::from("close_sale"))))?;
let payload = mcp.tools_list_response();        // -> MCP tools/list result JSON
if mcp.has_changed() { /* emit notifications/tools/list_changed */ mcp.acknowledge_change(); }
```
