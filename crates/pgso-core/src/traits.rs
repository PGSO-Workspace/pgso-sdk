use crate::{action::ScopeDecision, error::ActuatorError, types::{AudioWindow, Catalog, SignalReading}};

/// Perception source. Implementations are stateful (may maintain running statistics).
pub trait Signal {
    /// Extract readings from an audio window.
    /// Returns one reading per axis available; empty if silent/sub-VAD.
    fn extract(&mut self, window: &AudioWindow) -> Vec<SignalReading>;
}

/// Exposure point where governance decisions mutate the tool catalog.
pub trait Actuator {
    /// The catalog currently served to the agent.
    fn current_catalog(&self) -> Catalog;

    /// Apply a governance decision and return the resulting served catalog.
    ///
    /// # Errors
    ///
    /// Returns [`ActuatorError`] if the transport cannot apply the decision
    /// (e.g. an unknown tool id or a transport/serialization failure). The
    /// in-memory reference actuator is infallible.
    fn apply(&mut self, decision: &ScopeDecision) -> Result<Catalog, ActuatorError>;
}
