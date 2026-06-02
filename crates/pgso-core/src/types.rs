/// Opaque identifier for a tool in the agent's catalog.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ToolId(String);

impl ToolId {
    pub fn as_str(&self) -> &str { &self.0 }
}

impl std::fmt::Display for ToolId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl From<&str> for ToolId {
    fn from(s: &str) -> Self { Self(s.to_string()) }
}

impl From<String> for ToolId {
    fn from(s: String) -> Self { Self(s) }
}

/// A tool in the agent's catalog.
#[derive(Debug, Clone, PartialEq)]
pub struct Tool {
    pub id: ToolId,
    pub name: String,
    pub requires_step_up: bool,
}

impl Tool {
    pub fn new(id: impl Into<ToolId>, name: impl Into<String>) -> Self {
        Self { id: id.into(), name: name.into(), requires_step_up: false }
    }
}

/// An ordered collection of tools exposed to the agent.
#[derive(Debug, Clone, PartialEq)]
pub struct Catalog {
    tools: Vec<Tool>,
}

impl Catalog {
    pub fn new(tools: Vec<Tool>) -> Self { Self { tools } }
    pub fn tools(&self) -> &[Tool] { &self.tools }
    pub fn len(&self) -> usize { self.tools.len() }
    pub fn is_empty(&self) -> bool { self.tools.is_empty() }
    pub fn contains(&self, id: &ToolId) -> bool { self.tools.iter().any(|t| t.id == *id) }
    pub fn find(&self, id: &ToolId) -> Option<&Tool> { self.tools.iter().find(|t| t.id == *id) }

    pub fn remove(&mut self, id: &ToolId) { self.tools.retain(|t| t.id != *id); }
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
    Valence,
    Arousal,
}

/// A single reading from a Signal extractor.
/// `value` is the raw per-channel model output, normalized to ~[0,1].
/// The DecisionEngine computes deviation relative to speaker baseline.
#[derive(Debug, Clone)]
pub struct SignalReading {
    pub value: f32,
    pub axis: Axis,
    pub confidence: f32,
    pub timestamp_ms: u64,
}

/// A window of raw audio samples for signal extraction.
#[derive(Debug, Clone)]
pub struct AudioWindow {
    pub samples: Vec<f32>,   // mono, resampled to model rate
    pub sample_rate: u32,
    pub timestamp_ms: u64,   // caller-supplied, NOT from wall-clock
}
