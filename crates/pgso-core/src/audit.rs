//! The [`AuditLog`]: an append-only, in-memory record of emitted
//! [`ScopeDecision`]s for full auditability (G5 / INV-5).
//!
//! The log stores clones of decisions (each already carrying its
//! [`crate::action::AuditRecord`]), so the trail survives even if the original
//! decisions are consumed downstream.

use crate::action::ScopeDecision;

/// A committed state transition, including removals and their triggering input.
#[derive(Debug, Clone)]
pub struct PolicyTransition {
    /// Caller-supplied observation or expiry time.
    pub timestamp_ms: u64,
    /// Observation; None denotes explicit expiry by the trusted host.
    pub reading: Option<crate::SignalReading>,
    /// State before reconciliation.
    pub before: crate::GovernanceState,
    /// State after reconciliation.
    pub after: crate::GovernanceState,
    /// All surviving contributions, including their rule identifiers.
    pub active: Vec<ScopeDecision>,
}

/// An append-only, in-memory log of governance decisions.
///
/// Holds clones of every recorded [`ScopeDecision`] in insertion order, giving a
/// transition trail. It omits no-op observations and failed attempts; it is not
/// a complete input replay dataset. Pure in-memory: no I/O, consistent
/// with INV-1.
#[derive(Debug, Clone, Default)]
pub struct AuditLog {
    entries: Vec<ScopeDecision>,
    transitions: Vec<PolicyTransition>,
}

impl AuditLog {
    /// Create an empty log.
    #[must_use]
    pub const fn new() -> Self {
        Self {
            entries: Vec::new(),
            transitions: Vec::new(),
        }
    }

    /// Append a clone of `decision` to the log.
    pub fn record(&mut self, decision: &ScopeDecision) {
        self.entries.push(decision.clone());
    }

    /// Borrow the recorded decisions in insertion order.
    #[must_use]
    pub fn entries(&self) -> &[ScopeDecision] {
        &self.entries
    }

    /// Committed complete-state transitions; duplicate/no-op updates are omitted.
    pub fn transitions(&self) -> &[PolicyTransition] {
        &self.transitions
    }

    pub(crate) fn transition(&mut self, transition: PolicyTransition) {
        self.transitions.push(transition);
    }

    /// Remove all recorded decisions.
    pub fn clear(&mut self) {
        self.entries.clear();
        self.transitions.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Action, ToolId};

    #[test]
    fn test_audit_log_records_and_retrieves() {
        let mut log = AuditLog::new();
        let d = ScopeDecision::new(Action::RequireStepUp(ToolId::from("search")));
        log.record(&d);
        assert_eq!(log.entries().len(), 1);
    }

    #[test]
    fn test_audit_log_clear() {
        let mut log = AuditLog::new();
        log.record(&ScopeDecision::new(Action::Allow));
        log.clear();
        assert!(log.entries().is_empty());
    }
}
