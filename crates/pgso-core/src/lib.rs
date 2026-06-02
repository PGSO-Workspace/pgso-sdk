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

pub mod types;
pub mod action;
pub mod error;
pub mod traits;
pub mod engine;
pub mod rules;
pub mod audit;
pub mod pipeline;

pub use types::*;
pub use action::*;
pub use error::*;
pub use traits::*;
pub use engine::{DecisionEngine, EngineConfig, EngineOutput};
pub use rules::{Rule, RuleEngine, RulePredicate, RuleSet};
pub use audit::AuditLog;
pub use pipeline::{Pgso, PgsoBuildError, PgsoBuilder};
