//! In-memory policy actuator using the shared deterministic state implementation.
#![deny(missing_docs)]
use pgso_core::{Actuator, ActuatorError, Catalog, GovernanceState, ScopeDecision, ToolId};
use std::collections::HashSet;
/// In-memory reference actuator; reconciliation commits one complete state.
#[derive(Debug, Clone)]
pub struct LocalActuator {
    base_catalog: Catalog,
    state: GovernanceState,
    protected: HashSet<ToolId>,
}
impl LocalActuator {
    /// Create a nominal actuator with tools that cannot be withdrawn.
    pub fn new(catalog: Catalog, protected: HashSet<ToolId>) -> Self {
        Self {
            state: GovernanceState::nominal(catalog.clone()),
            base_catalog: catalog,
            protected,
        }
    }
    /// Active context blocks.
    pub fn directives(&self) -> &[String] {
        &self.state.directives
    }
}
impl Actuator for LocalActuator {
    fn current_state(&self) -> GovernanceState {
        self.state.clone()
    }
    fn current_catalog(&self) -> Catalog {
        self.state.catalog.clone()
    }
    fn apply(&mut self, decision: &ScopeDecision) -> Result<Catalog, ActuatorError> {
        self.state
            .apply(&decision.action, &self.base_catalog, &self.protected);
        Ok(self.current_catalog())
    }
    fn reconcile(&mut self, active: &[ScopeDecision]) -> Result<Catalog, ActuatorError> {
        self.state = GovernanceState::reconcile(&self.base_catalog, &self.protected, active);
        Ok(self.current_catalog())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use pgso_core::{Action, Tool};

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
        act.apply(&ScopeDecision::new(Action::Prune(close_sale.clone())))
            .unwrap();
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
