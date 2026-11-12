//! The [`AuditLog`]: an append-only, in-memory record of emitted
//! [`ScopeDecision`]s for full auditability (G5 / INV-5).
//!
//! The log stores clones of decisions (each already carrying its
//! [`crate::action::AuditRecord`]), so the trail survives even if the original
//! decisions are consumed downstream.

use crate::action::ScopeDecision;

/// Why a committed transition happened, recorded at the event that caused it (never inferred later).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum TransitionCause {
    /// A triggered reading produced contributions on an axis that had none.
    Activation,
    /// A triggered reading replaced the axis' contributions with a different, non-empty rule set.
    Replacement,
    /// The engine judged the reading nominal; the axis' contributions were retired.
    NominalReading,
    /// The engine triggered but no rule matched (e.g. an opposite-direction run under a
    /// direction-restricted rule); the axis' contributions were retired.
    NoMatchingRule,
    /// The trusted host's explicit expiry retired contributions older than the cutoff.
    HostExpiry,
}

impl TransitionCause {
    /// Stable lowercase identifier for logs and exports.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Activation => "activation",
            Self::Replacement => "replacement",
            Self::NominalReading => "nominal_reading",
            Self::NoMatchingRule => "no_matching_rule",
            Self::HostExpiry => "host_expiry",
        }
    }

    /// True when the transition removed contributions rather than adding or replacing them.
    #[must_use]
    pub const fn is_retirement(self) -> bool {
        matches!(
            self,
            Self::NominalReading | Self::NoMatchingRule | Self::HostExpiry
        )
    }
}

/// A committed state transition, including removals and their triggering input.
#[derive(Debug, Clone)]
pub struct PolicyTransition {
    /// Caller-supplied observation or expiry time.
    pub timestamp_ms: u64,
    /// Evidence-age cutoff used for expiry; None for observation transitions.
    pub expiry_cutoff_ms: Option<u64>,
    /// Observation; None denotes explicit expiry by the trusted host.
    pub reading: Option<crate::SignalReading>,
    /// State before reconciliation.
    pub before: crate::GovernanceState,
    /// State after reconciliation.
    pub after: crate::GovernanceState,
    /// All surviving contributions, including their rule identifiers.
    pub active: Vec<ScopeDecision>,
    /// Cause recorded at the triggering event (reading outcome or host expiry).
    pub cause: TransitionCause,
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
