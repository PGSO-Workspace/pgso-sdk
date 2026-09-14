//! Pure, deterministic core of the PGSO SDK: prosody signal governs the agent's
//! tool catalog.
//!
//! Perception is probabilistic ([`Signal`] readings carry confidence); action is
//! deterministic and verifiable. This crate has zero ML/HTTP/IO dependencies: the
//! [`DecisionEngine`] turns [`SignalReading`]s into sustained-deviation
//! [`EngineOutput`]s, the [`RuleEngine`] maps those to governance [`Action`]s, and
//! the [`Pgso`] pipeline applies them to an [`Actuator`]'s catalog while recording
//! an [`AuditLog`].

#![deny(missing_docs)]
// The core is unsafe-free by construction; make that a compiler guarantee, not a
// review convention. Any `unsafe` introduced anywhere in this crate (including
// its in-crate `#[cfg(test)]` modules) becomes a hard compile error.
#![forbid(unsafe_code)]

pub mod action;
pub mod audit;
pub mod engine;
pub mod error;
pub mod pipeline;
pub mod rules;
pub mod state;
pub mod traits;
pub mod types;

pub use action::*;
pub use audit::AuditLog;
pub use engine::{DecisionEngine, EngineConfig, EngineOutput};
pub use error::*;
pub use pipeline::{Pgso, PgsoBuildError, PgsoBuilder};
pub use rules::{Direction, Rule, RuleEngine, RulePredicate, RuleSet};
pub use state::GovernanceState;
pub use traits::*;
pub use types::*;
