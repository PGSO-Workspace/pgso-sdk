//! Core data types: tool catalogs, paralinguistic axes, and signal/audio
//! inputs shared across the perception and governance layers.

/// Opaque identifier for a tool in the agent's catalog.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ToolId(String);

impl ToolId {
    /// Borrow the identifier as a string slice.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Display for ToolId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl From<&str> for ToolId {
    fn from(s: &str) -> Self {
        Self(s.to_string())
    }
}

impl From<String> for ToolId {
    fn from(s: String) -> Self {
        Self(s)
    }
}

/// A tool in the agent's catalog.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Tool {
    /// Opaque identifier used to address the tool in catalogs and actions.
    pub id: ToolId,
    /// Human-readable name of the tool.
    pub name: String,
    /// Whether the tool requires confirmation (step-up) before use.
    pub requires_step_up: bool,
}

impl Tool {
    /// Create a tool with the given id and name; `requires_step_up` defaults to `false`.
    pub fn new(id: impl Into<ToolId>, name: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            name: name.into(),
            requires_step_up: false,
        }
    }
}

/// An ordered collection of tools exposed to the agent.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Catalog {
    tools: Vec<Tool>,
}

impl Catalog {
    /// Create a catalog from an ordered list of tools.
    #[must_use]
    pub const fn new(tools: Vec<Tool>) -> Self {
        Self { tools }
    }
    /// Borrow the tools in catalog order.
    #[must_use]
    pub fn tools(&self) -> &[Tool] {
        &self.tools
    }
    /// Number of tools in the catalog.
    #[must_use]
    pub fn len(&self) -> usize {
        self.tools.len()
    }
    /// Whether the catalog contains no tools.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.tools.is_empty()
    }
    /// Whether a tool with the given id is present.
    #[must_use]
    pub fn contains(&self, id: &ToolId) -> bool {
        self.tools.iter().any(|t| t.id == *id)
    }
    /// Borrow the tool with the given id, if present.
    #[must_use]
    pub fn find(&self, id: &ToolId) -> Option<&Tool> {
        self.tools.iter().find(|t| t.id == *id)
    }

    /// Remove the tool with the given id, if present (no-op otherwise).
    pub fn remove(&mut self, id: &ToolId) {
        self.tools.retain(|t| t.id != *id);
    }
    /// Set the `requires_step_up` flag on the tool with the given id, if present.
    pub fn set_step_up(&mut self, id: &ToolId, flag: bool) {
        if let Some(t) = self.tools.iter_mut().find(|t| t.id == *id) {
            t.requires_step_up = flag;
        }
    }
}

/// The paralinguistic axis being measured.
/// Dominance is excluded in v1 (Phase 0 discarded it).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Axis {
    /// Pleasantness axis (negative to positive affect).
    Valence,
    /// Activation axis (calm to excited).
    Arousal,
}

/// A single reading from a Signal extractor.
/// `value` is the raw per-channel model output, normalized to roughly `[0, 1]`.
/// The `DecisionEngine` computes deviation relative to speaker baseline.
#[derive(Debug, Clone)]
pub struct SignalReading {
    /// Raw per-channel model output for the axis, normalized to roughly `[0, 1]`.
    /// The engine computes deviation relative to the speaker baseline.
    pub value: f32,
    /// The paralinguistic axis this reading measures.
    pub axis: Axis,
    /// Model confidence in this reading, in `[0, 1]`. Readings below the
    /// engine's confidence threshold are abstained on (G4).
    pub confidence: f32,
    /// Caller-supplied timestamp in milliseconds (not taken from wall-clock).
    pub timestamp_ms: u64,
}

/// A window of raw audio samples for signal extraction.
#[derive(Debug, Clone)]
pub struct AudioWindow {
    /// Mono PCM samples, resampled to the model's expected rate.
    pub samples: Vec<f32>, // mono, resampled to model rate
    /// Sample rate of `samples` in Hz.
    pub sample_rate: u32,
    /// Caller-supplied timestamp in milliseconds (not taken from wall-clock).
    pub timestamp_ms: u64, // caller-supplied, NOT from wall-clock
}
