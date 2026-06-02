//! Pure-Rust eGeMAPS-style DSP signal extractor — the default, auditable PGSO signal.
//!
//! Computes an interpretable subset of low-level descriptors (F0, energy, jitter,
//! shimmer, voicing) from raw audio, with no ML runtime and no proprietary
//! dependency. Each `SignalReading` is explainable via [`ExplainedReading`].

#![allow(dead_code)]

mod dsp;
mod features;
mod windowing;

// pub use features::{AcousticFeatures, AxisMapping}; // wired up in Task 4
// EgemapsSignal, ExplainedReading defined below in Task 4.
