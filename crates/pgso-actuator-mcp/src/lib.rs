//! `pgso-actuator-mcp`: an [`Actuator`] that exposes the governed tool catalog
//! over the **Model Context Protocol** (MCP) `tools/list` transport.
//!
//! This crate is the *transport-agnosticism proof* for PGSO (Milestone 5). It
//! implements the exact same [`Actuator`] boundary as `pgso-actuator-local`,
//! with identical governance semantics (G1/G2/G3), but additionally renders the
//! served catalog as an MCP `tools/list` JSON payload and tracks whether a
//! `notifications/tools/list_changed` should be emitted. The deterministic
//! [`pgso_core`] engine, rules, and pipeline drive it **without any change** —
//! its current reconciliation uses the shared core policy state.
//!
//! ## MCP shape (verified against the 2025-06-18 MCP spec)
//!
//! The `tools/list` *result* object is:
//!
//! ```json
//! { "tools": [ { "name": "...", "title": "...", "description": "...",
//!               "inputSchema": { "type": "object", "properties": {}, "required": [] } } ] }
//! ```
//!
//! [`McpActuator::tools_list_response`] returns exactly this `result` object
//! (the JSON-RPC envelope — `jsonrpc`/`id`/`result` — and pagination
//! `nextCursor` are the responsibility of the surrounding MCP server and are out
//! of scope for the governance layer). `notifications/tools/list_changed` is a
//! server→client notification with no `params`, emitted when the served catalog
//! changes; [`McpActuator::has_changed`] / [`McpActuator::acknowledge_change`]
//! track exactly that.
//!
//! ## No panics (master spec §5)
//!
//! Nothing in this module's library code panics: [`McpActuator::apply`]
//! propagates [`ActuatorError`] via its `Result`, and the JSON is built with
//! [`serde_json::json!`], which is infallible for the owned `String`/`Value`
//! inputs used here (no fallible serialization, no `.unwrap()`). `.unwrap()`
//! appears only under `#[cfg(test)]`.

#![deny(missing_docs)]

use pgso_core::{Actuator, ActuatorError, Catalog, GovernanceState, ScopeDecision, ToolId};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};

/// Host-owned tool metadata; schemas are also validated by the executor.
#[derive(Debug, Clone)]
pub struct ToolDefinition {
    /// Description supplied to the model.
    pub description: String,
    /// JSON Schema for the argument object.
    pub input_schema: Value,
}

/// An [`Actuator`] that serves the governed catalog as MCP `tools/list`
/// payloads.
///
/// Construct one with [`McpActuator::new`], drive it through the standard
/// [`Actuator`] interface (so it slots into [`pgso_core::Pgso`] unchanged), then
/// read [`McpActuator::tools_list_response`] for the MCP payload and
/// [`McpActuator::has_changed`] to decide whether to emit
/// `notifications/tools/list_changed`.
#[derive(Debug)]
pub struct McpActuator {
    /// The full nominal catalog, restored on [`pgso_core::Action::Allow`].
    base_catalog: Catalog,
    /// The catalog currently served over MCP (after governance).
    state: GovernanceState,
    /// Tool ids that are never pruned (G2 inviolable allowlist).
    protected: HashSet<ToolId>,
    /// Set when the served catalog changes; cleared by
    /// [`McpActuator::acknowledge_change`]. Drives
    /// `notifications/tools/list_changed`.
    changed: bool,
    definitions: HashMap<ToolId, ToolDefinition>,
}

impl McpActuator {
    /// Set metadata for a nominal tool.
    ///
    /// # Errors
    /// Returns an error for unknown tools or schemas without object type.
    pub fn define(&mut self, id: ToolId, definition: ToolDefinition) -> Result<(), ActuatorError> {
        if !self.base_catalog.contains(&id) {
            return Err(ActuatorError::UnknownTool(id));
        }
        if definition.input_schema.get("type") != Some(&json!("object")) {
            return Err(ActuatorError::Internal(
                "inputSchema must describe an object".into(),
            ));
        }
        self.definitions.insert(id, definition);
        self.changed = true;
        Ok(())
    }
    /// Create an actuator serving `catalog`, treating every id in `protected` as
    /// inviolable (never pruned — G2).
    #[must_use]
    pub fn new(catalog: Catalog, protected: HashSet<ToolId>) -> Self {
        Self {
            state: GovernanceState::nominal(catalog.clone()),
            base_catalog: catalog,
            protected,
            changed: false,
            definitions: HashMap::new(),
        }
    }

    /// Render the currently-served catalog as the MCP `tools/list` *result*
    /// object: `{ "tools": [ { name, title, description, inputSchema } ] }`.
    ///
    /// Pruned tools are absent; protected tools that a rule targeted are still
    /// present (G2 over MCP — REQ-5.6). The shape matches the 2025-06-18 MCP
    /// spec; see the [module docs](self) for what is intentionally left to the
    /// surrounding MCP server (the JSON-RPC envelope and pagination).
    ///
    /// This is infallible: it builds owned [`serde_json::Value`]s with
    /// [`serde_json::json!`] and never serializes a fallible type, so it cannot
    /// panic (master spec §5).
    #[must_use]
    pub fn tools_list_response(&self) -> Value {
        let tools: Vec<Value> = self
            .state
            .catalog
            .tools()
            .iter()
            .map(|t| {
                // Per the MCP spec, `name` is the programmatic identifier a
                // client sends in `tools/call`; `title` is the human display
                // label. So the stable `ToolId` is the name and `Tool::name`
                // (the label) is the title.
                json!({
                    "name": t.id.as_str(),
                    "title": t.name,
                    "description": self.definitions.get(&t.id).map_or_else(|| format!("The {} tool.", t.name), |d| d.description.clone()),
                    "_meta": { "pgso/requiresStepUp": t.requires_step_up },
                    "inputSchema": self.definitions.get(&t.id).map_or_else(|| json!({"type":"object", "properties":{}, "required":[], "additionalProperties":false}), |d| d.input_schema.clone())
                })
            })
            .collect();
        json!({ "tools": tools })
    }

    /// Whether the served catalog has changed since the last
    /// [`McpActuator::acknowledge_change`] (i.e. whether a
    /// `notifications/tools/list_changed` is owed to the client).
    ///
    /// Set **only** when the served catalog actually changed — an
    /// [`pgso_core::Action::Allow`] on an already-nominal catalog, a no-op `Prune` of an
    /// absent/protected tool, or an [`pgso_core::Action::InjectDirective`] (which never
    /// affects the tools payload) does not flip this flag.
    #[must_use]
    pub const fn has_changed(&self) -> bool {
        self.changed
    }

    /// Clear the change flag after a `notifications/tools/list_changed` has been
    /// emitted.
    pub const fn acknowledge_change(&mut self) {
        self.changed = false;
    }

    /// The directive blocks currently appended to the agent context (G1).
    /// Mirrors `LocalActuator::directives`; not part of the MCP tools payload.
    #[must_use]
    pub fn directives(&self) -> &[String] {
        &self.state.directives
    }
}

impl Actuator for McpActuator {
    fn current_state(&self) -> GovernanceState {
        self.state.clone()
    }
    fn current_catalog(&self) -> Catalog {
        self.state.catalog.clone()
    }
    fn apply(&mut self, decision: &ScopeDecision) -> Result<Catalog, ActuatorError> {
        let before = self.state.catalog.clone();
        self.state
            .apply(&decision.action, &self.base_catalog, &self.protected);
        self.changed |= before != self.state.catalog;
        Ok(self.current_catalog())
    }
    fn reconcile(&mut self, active: &[ScopeDecision]) -> Result<Catalog, ActuatorError> {
        let next = GovernanceState::reconcile(&self.base_catalog, &self.protected, active);
        self.changed |= next.catalog != self.state.catalog;
        self.state = next;
        Ok(self.current_catalog())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pgso_core::{Action, Tool};

    fn fixture() -> McpActuator {
        let tools = vec![
            Tool::new("search", "Web Search"),
            Tool::new("calculate", "Calculator"),
            Tool::new("close_sale", "Close Sale"),
            Tool::new("escalate", "Escalate to Human"),
        ];
        let protected = HashSet::from([ToolId::from("escalate")]);
        McpActuator::new(Catalog::new(tools), protected)
    }

    #[test]
    fn test_mcp_adapter_implements_actuator() {
        // REQ-5.1: object-safe — McpActuator is usable as `Box<dyn Actuator>`.
        let act: Box<dyn Actuator> = Box::new(fixture());
        assert_eq!(act.current_catalog().len(), 4);
    }

    #[test]
    fn test_mcp_prune_omits_tool_from_list() {
        // REQ-5.2: a pruned tool is absent from the tools/list payload.
        let mut act = fixture();
        act.apply(&ScopeDecision::new(Action::Prune(ToolId::from(
            "close_sale",
        ))))
        .unwrap();

        let payload = act.tools_list_response();
        let tools = payload["tools"].as_array().unwrap();
        // `name` is the programmatic id (MCP spec); pruned id must be absent.
        let names: Vec<&str> = tools.iter().map(|t| t["name"].as_str().unwrap()).collect();
        assert!(!names.contains(&"close_sale"));
        assert_eq!(tools.len(), 3);
    }

    #[test]
    fn test_mcp_protected_tool_survives() {
        // REQ-5.6 (G2 over MCP): a rule targeting a protected tool leaves it in
        // the served list. (Here applied directly; the RuleEngine also downgrades
        // Prune→RequireStepUp upstream, but the actuator alone must also hold.)
        let mut act = fixture();
        act.apply(&ScopeDecision::new(Action::Prune(ToolId::from("escalate"))))
            .unwrap();

        let payload = act.tools_list_response();
        let tools = payload["tools"].as_array().unwrap();
        let names: Vec<&str> = tools.iter().map(|t| t["name"].as_str().unwrap()).collect();
        assert!(
            names.contains(&"escalate"),
            "G2: protected tool must survive"
        );
    }

    #[test]
    fn test_mcp_allow_restores_full_list() {
        // G1: Allow restores the full catalog over MCP.
        let mut act = fixture();
        act.apply(&ScopeDecision::new(Action::Prune(ToolId::from(
            "close_sale",
        ))))
        .unwrap();
        act.apply(&ScopeDecision::new(Action::Allow)).unwrap();

        let payload = act.tools_list_response();
        let tools = payload["tools"].as_array().unwrap();
        assert_eq!(tools.len(), 4);
    }

    #[test]
    fn test_mcp_has_changed_flag() {
        // notifications/tools/list_changed bookkeeping.
        let mut act = fixture();
        assert!(!act.has_changed());
        act.apply(&ScopeDecision::new(Action::Prune(ToolId::from(
            "close_sale",
        ))))
        .unwrap();
        assert!(act.has_changed());
        act.acknowledge_change();
        assert!(!act.has_changed());
    }

    #[test]
    fn test_mcp_has_changed_unset_on_noop_prune() {
        // The flag tracks REAL changes only: pruning a protected tool (a no-op
        // for the served catalog) must NOT request a list_changed notification.
        let mut act = fixture();
        act.apply(&ScopeDecision::new(Action::Prune(ToolId::from("escalate"))))
            .unwrap();
        assert!(
            !act.has_changed(),
            "no-op prune of a protected tool must not flip the change flag"
        );
    }

    #[test]
    fn test_mcp_has_changed_unset_on_directive() {
        // InjectDirective never alters the served tools list, so it owes no
        // list_changed notification.
        let mut act = fixture();
        act.apply(&ScopeDecision::new(Action::InjectDirective(
            "De-escalate.".into(),
        )))
        .unwrap();
        assert!(!act.has_changed());
        assert_eq!(act.directives(), &["De-escalate.".to_string()]);
    }

    #[test]
    fn test_mcp_payload_shape_matches_spec() {
        // R8: each tool object carries name/title/description and a well-formed
        // inputSchema (type=object, properties present), per the MCP spec.
        let act = fixture();
        let payload = act.tools_list_response();
        let first = &payload["tools"][0];
        // Lock in the spec mapping: name = programmatic id, title = display label.
        // (Fixture's first tool is Tool::new("search", "Web Search").)
        assert_eq!(first["name"], "search");
        assert_eq!(first["title"], "Web Search");
        assert!(first["description"].is_string());
        assert_eq!(first["inputSchema"]["type"], "object");
        assert!(first["inputSchema"]["properties"].is_object());
    }

    #[test]
    fn test_mcp_stepup_keeps_tool_in_list() {
        // RequireStepUp keeps the tool served (friction, not removal — G3).
        let mut act = fixture();
        act.apply(&ScopeDecision::new(Action::RequireStepUp(ToolId::from(
            "close_sale",
        ))))
        .unwrap();
        let payload = act.tools_list_response();
        let tools = payload["tools"].as_array().unwrap();
        let names: Vec<&str> = tools.iter().map(|t| t["name"].as_str().unwrap()).collect();
        assert!(names.contains(&"close_sale"));
        assert_eq!(tools.len(), 4);
        // RequireStepUp changes the served catalog (the tool's flag), so the
        // change flag is set.
        assert!(act.has_changed());
    }
}
