use crate::types::{Axis, ToolId};

/// A governance action emitted by the rule engine.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub enum Action {
    /// No governance needed; when applied, restores catalog to nominal.
    Allow,
    /// Keep the tool but require confirmation before use.
    RequireStepUp(ToolId),
    /// Remove the tool from the served catalog (never for protected tools).
    Prune(ToolId),
    /// Append a demarcated directive block to the agent's context.
    InjectDirective(String),
}

/// Audit trail for a governance decision (INV-5).
#[derive(Debug, Clone)]
pub struct AuditRecord {
    pub timestamp_ms: u64,
    pub signal_value: Option<f32>,
    pub axis: Option<Axis>,
    pub deviation: Option<f32>,
    pub threshold_crossed: Option<f32>,
    pub rule_id: Option<String>,
}

impl AuditRecord {
    /// Empty audit record for hand-constructed decisions in tests.
    pub fn empty() -> Self {
        Self {
            timestamp_ms: 0, signal_value: None, axis: None,
            deviation: None, threshold_crossed: None, rule_id: None,
        }
    }
}

/// A governance decision: an action paired with its audit trail.
#[derive(Debug, Clone)]
pub struct ScopeDecision {
    pub action: Action,
    pub audit: AuditRecord,
}

impl ScopeDecision {
    /// Decision with an empty audit record (for M1 tests).
    pub fn new(action: Action) -> Self {
        Self { action, audit: AuditRecord::empty() }
    }

    pub fn with_audit(action: Action, audit: AuditRecord) -> Self {
        Self { action, audit }
    }
}
