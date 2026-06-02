use crate::types::ToolId;

#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum ActuatorError {
    #[error("unknown tool id: {0}")]
    UnknownTool(ToolId),
    #[error("actuator error: {0}")]
    Internal(String),
}
