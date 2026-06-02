//! `pgso-actuator-local`: the in-memory reference [`Actuator`].
//!
//! Holds the tool catalog in memory and applies governance decisions
//! deterministically and infallibly (no I/O). It enforces the inviolable
//! allowlist (G2 — protected tools are never pruned), restores to nominal on
//! `Allow` (G1 — including clearing injected directives), and applies
//! `Prune`/`RequireStepUp` idempotently so the pipeline may replay a decision
//! across windows while a deviation is sustained.

#![deny(missing_docs)]

use std::collections::HashSet;
use pgso_core::{
    Action, Actuator, ActuatorError, Catalog, ScopeDecision, ToolId,
};

/// In-memory [`Actuator`]: applies governance decisions to a tool catalog held
/// in memory, honouring a set of protected (never-pruned) tools.
pub struct LocalActuator {
    base_catalog: Catalog,
    active_catalog: Catalog,
    protected: HashSet<ToolId>,
    directive_blocks: Vec<String>,
}

impl LocalActuator {
    /// Create an actuator serving `catalog`, treating tool ids in `protected` as
    /// inviolable (never pruned, per G2).
    #[must_use]
    pub fn new(catalog: Catalog, protected: HashSet<ToolId>) -> Self {
        Self {
            active_catalog: catalog.clone(),
            base_catalog: catalog,
            protected,
            directive_blocks: Vec::new(),
        }
    }

    /// Borrow the directive blocks currently injected into the agent's context
    /// (in injection order); cleared when the catalog is restored to nominal.
    #[must_use]
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
                // G2: protected tools are never pruned. Pruning a tool that is
                // absent (already pruned, or unknown id) is an intentional,
                // idempotent no-op — the end-to-end pipeline (M4) replays the
                // same decision across consecutive windows while a deviation is
                // sustained, so this MUST be safe to apply repeatedly. The
                // decision's *intent* is recorded upstream in the AuditLog (G5),
                // so tolerance here does not lose the audit signal.
                if !self.protected.contains(id) {
                    self.active_catalog.remove(id);
                }
            }
            Action::RequireStepUp(id) => {
                // Idempotent: setting the flag on an absent tool is a no-op
                // (see the Prune rationale above).
                self.active_catalog.set_step_up(id, true);
            }
            Action::InjectDirective(text) => {
                self.directive_blocks.push(text.clone());
            }
            // `Action` is `#[non_exhaustive]`; a variant added in a later
            // milestone reaches this arm. Release behavior: leave the catalog
            // unchanged (governing principle — PGSO fails toward inaction).
            // Debug/test builds trip this assertion so an unhandled variant is
            // caught loudly during development rather than silently ignored.
            _ => {
                debug_assert!(
                    false,
                    "unhandled #[non_exhaustive] Action variant in LocalActuator::apply"
                );
            }
        }
        Ok(self.active_catalog.clone())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pgso_core::Tool;

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
