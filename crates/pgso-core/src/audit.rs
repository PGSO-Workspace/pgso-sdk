//! The [`AuditLog`]: an append-only, in-memory record of emitted
//! [`ScopeDecision`]s for full auditability (G5 / INV-5).
//!
//! The log stores clones of decisions (each already carrying its
//! [`crate::action::AuditRecord`]), so the trail survives even if the original
//! decisions are consumed downstream.

use crate::action::ScopeDecision;

/// An append-only, in-memory log of governance decisions.
///
/// Holds clones of every recorded [`ScopeDecision`] in insertion order, giving a
/// complete, replayable audit trail (G5). Pure in-memory: no I/O, consistent
/// with INV-1.
#[derive(Debug, Clone, Default)]
pub struct AuditLog {
    entries: Vec<ScopeDecision>,
}

impl AuditLog {
    /// Create an empty log.
    #[must_use]
    pub fn new() -> Self {
        Self { entries: Vec::new() }
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

    /// Remove all recorded decisions.
    pub fn clear(&mut self) {
        self.entries.clear();
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
