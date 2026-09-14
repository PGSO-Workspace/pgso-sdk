//! Effective policy state shared by all reference actuators.
use crate::{Action, Catalog, ScopeDecision, ToolId};
use std::collections::HashSet;

/// Observable state; directives are included when detecting policy changes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GovernanceState {
    /// Currently exposed tools and confirmation requirements.
    pub catalog: Catalog,
    /// Active, deduplicated context blocks in deterministic order.
    pub directives: Vec<String>,
}

impl GovernanceState {
    /// A nominal state with no transient directives.
    pub const fn nominal(catalog: Catalog) -> Self {
        Self {
            catalog,
            directives: Vec::new(),
        }
    }

    /// Apply a direct action. Allow resets this state; repeated directives are idempotent.
    pub fn apply(&mut self, action: &Action, base: &Catalog, protected: &HashSet<ToolId>) {
        match action {
            Action::Allow => *self = Self::nominal(base.clone()),
            Action::Prune(id) => {
                if !protected.contains(id) {
                    self.catalog.remove(id);
                }
            }
            Action::RequireStepUp(id) => self.catalog.set_step_up(id, true),
            Action::InjectDirective(text) => {
                if !self.directives.contains(text) {
                    self.directives.push(text.clone());
                }
            }
        }
    }

    /// Recompute from all active contributions, so expired rules leave no residue.
    /// Allow contributes no restriction and cannot override another active rule.
    pub fn reconcile(
        base: &Catalog,
        protected: &HashSet<ToolId>,
        active: &[ScopeDecision],
    ) -> Self {
        let mut state = Self::nominal(base.clone());
        for decision in active {
            if !matches!(decision.action, Action::Allow) {
                state.apply(&decision.action, base, protected);
            }
        }
        state
    }
}
