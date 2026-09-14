//! Error types for the actuator boundary.

use crate::types::ToolId;

/// Invalid configuration supplied by an integrator.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("invalid configuration: {0}")]
pub struct ConfigError(pub &'static str);

/// Error surface for [`crate::traits::Actuator`] implementations.
///
/// The in-memory reference actuator (`LocalActuator`) is infallible by design:
/// applying a decision can never fail because there is no I/O. These variants
/// exist for fallible transports added in later milestones — e.g. the MCP/HTTP
/// adapters, which can hit serialization, protocol, or network errors. The enum
/// is `#[non_exhaustive]` so transports may add their own variants without a
/// breaking change.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum ActuatorError {
    /// A transport could not resolve a tool id against its backing catalog.
    #[error("unknown tool id: {0}")]
    UnknownTool(ToolId),
    /// Transport-internal failure (network, serialization, protocol).
    #[error("actuator error: {0}")]
    Internal(String),
}
