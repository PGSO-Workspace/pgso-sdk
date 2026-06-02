//! The [`Pgso`] pipeline: the end-to-end governance loop (Milestone 4).
//!
//! [`Pgso`] wires the four stages of the SDK into one driver:
//!
//! ```text
//! Signal::extract -> DecisionEngine::process -> RuleEngine::evaluate -> Actuator::apply
//! ```
//!
//! It is **generic** over the [`Signal`] and [`Actuator`] traits and holds no
//! concrete extractor or transport, so `pgso-core` gains no new normal
//! dependency and INV-1 (pure core) still holds — R6 explicitly permits this
//! generic builder in core.
//!
//! ## Recovery semantics (level-triggered)
//!
//! The [`DecisionEngine`] is *level-triggered* (see [`DecisionEngine::process`]):
//! while a deviation stays sustained it returns `Some` on every reading, and
//! `None` the moment the deviation subsides. [`Pgso::process_window`] turns that
//! into catalog governance:
//!
//! - For each reading that triggers, the matching rules' decisions are applied
//!   to the actuator and recorded in the [`AuditLog`] (G5).
//! - When a window produced readings but **none** triggered, an
//!   [`Action::Allow`] is applied to restore the catalog to nominal and drop any
//!   directive block (G1).
//! - When a window produced **no** readings at all (silence / sub-VAD), the
//!   restore is skipped: silence holds the current state rather than resetting
//!   it (G4 in spirit — absence of signal is not a signal to act).
//!
//! ## No panics (master spec §5)
//!
//! Nothing in this module panics. [`PgsoBuilder::build`] returns a typed
//! [`PgsoBuildError`] rather than unwrapping missing stages, and
//! [`Pgso::process_window`] propagates [`ActuatorError`] with `?`.

use crate::{
    action::{Action, AuditRecord, ScopeDecision},
    audit::AuditLog,
    engine::DecisionEngine,
    error::ActuatorError,
    rules::RuleEngine,
    traits::{Actuator, Signal},
    types::{AudioWindow, Catalog},
};

/// Error returned by [`PgsoBuilder::build`] when a required stage was not
/// supplied before building.
///
/// A missing stage is a wiring mistake by the SDK integrator, but the master
/// spec (§5) forbids panicking in library code — so it surfaces as a typed,
/// recoverable error instead of an `expect`.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum PgsoBuildError {
    /// No [`Signal`] source was supplied via [`PgsoBuilder::signal`].
    #[error("Pgso builder is missing a Signal source")]
    MissingSignal,
    /// No [`DecisionEngine`] was supplied via [`PgsoBuilder::engine`].
    #[error("Pgso builder is missing a DecisionEngine")]
    MissingEngine,
    /// No [`RuleEngine`] was supplied via [`PgsoBuilder::rules`].
    #[error("Pgso builder is missing a RuleEngine")]
    MissingRules,
    /// No [`Actuator`] was supplied via [`PgsoBuilder::actuator`].
    #[error("Pgso builder is missing an Actuator")]
    MissingActuator,
}

/// The end-to-end governance pipeline.
///
/// Construct one with [`Pgso::builder`], then drive it window-by-window with
/// [`Pgso::process_window`]. Generic over the perception source `S` and the
/// exposure point `A`; the deterministic core ([`DecisionEngine`],
/// [`RuleEngine`], [`AuditLog`]) is held concretely.
pub struct Pgso<S: Signal, A: Actuator> {
    signal: S,
    engine: DecisionEngine,
    rules: RuleEngine,
    actuator: A,
    audit_log: AuditLog,
}

impl<S: Signal, A: Actuator> Pgso<S, A> {
    /// Start building a pipeline. All four stages must be supplied before
    /// [`PgsoBuilder::build`].
    #[must_use]
    pub fn builder() -> PgsoBuilder<S, A> {
        PgsoBuilder::default()
    }

    /// Process one audio window through the full pipeline and return the
    /// resulting catalog.
    ///
    /// Drives Signal -> `DecisionEngine` -> `RuleEngine` -> Actuator for every
    /// reading the signal produced for this window. See the [module
    /// docs](self) for the level-triggered recovery semantics. Returns the
    /// catalog as served after any governance actions for this window.
    ///
    /// # Errors
    ///
    /// Returns [`ActuatorError`] if the actuator rejects an applied decision.
    #[must_use = "the returned catalog is the served catalog after this window"]
    pub fn process_window(&mut self, window: &AudioWindow) -> Result<Catalog, ActuatorError> {
        let readings = self.signal.extract(window);

        let mut any_triggered = false;
        for reading in &readings {
            if let Some(output) = self.engine.process(reading) {
                any_triggered = true;
                for decision in self.rules.evaluate(&output) {
                    self.actuator.apply(&decision)?;
                    self.audit_log.record(&decision);
                }
            }
        }

        // Level-triggered restore (G1): a window that carried readings but
        // triggered nothing means the deviation has subsided — restore the
        // catalog to nominal and drop any directive block via Action::Allow.
        // Silence (no readings) is deliberately NOT a restore trigger: absence
        // of signal holds the current state rather than resetting it.
        //
        // G5/REQ-4.6: a restore that actually changes the served catalog
        // (pruned -> full, directive cleared) is itself a catalog mutation and
        // MUST be auditable so the reversal is traceable. We record it only when
        // the catalog truly changed — a no-op Allow on an already-nominal catalog
        // (every calm window) is not a mutation and would only flood the log.
        if !any_triggered && !readings.is_empty() {
            let before = self.actuator.current_catalog();
            let timestamp_ms = readings.last().map_or(0, |r| r.timestamp_ms);
            let restore = ScopeDecision::with_audit(Action::Allow, AuditRecord::restore(timestamp_ms));
            let after = self.actuator.apply(&restore)?;
            if after != before {
                self.audit_log.record(&restore);
            }
        }

        Ok(self.actuator.current_catalog())
    }

    /// The catalog currently served by the actuator.
    #[must_use]
    pub fn current_catalog(&self) -> Catalog {
        self.actuator.current_catalog()
    }

    /// The append-only audit trail of every governance intervention applied so
    /// far (G5 / INV-5).
    #[must_use]
    pub const fn audit_log(&self) -> &AuditLog {
        &self.audit_log
    }
}

/// Builder for [`Pgso`]. Supply each stage, then call [`PgsoBuilder::build`].
///
/// `build` is fallible (returns [`PgsoBuildError`]) rather than panicking on a
/// missing stage, per the master spec's no-panic-in-library rule (§5).
pub struct PgsoBuilder<S: Signal, A: Actuator> {
    signal: Option<S>,
    engine: Option<DecisionEngine>,
    rules: Option<RuleEngine>,
    actuator: Option<A>,
}

impl<S: Signal, A: Actuator> Default for PgsoBuilder<S, A> {
    fn default() -> Self {
        Self { signal: None, engine: None, rules: None, actuator: None }
    }
}

impl<S: Signal, A: Actuator> PgsoBuilder<S, A> {
    /// Set the perception source.
    #[must_use]
    pub fn signal(mut self, signal: S) -> Self {
        self.signal = Some(signal);
        self
    }

    /// Set the deterministic decision engine.
    #[must_use]
    pub fn engine(mut self, engine: DecisionEngine) -> Self {
        self.engine = Some(engine);
        self
    }

    /// Set the rule engine (carrying the protected allowlist).
    #[must_use]
    pub fn rules(mut self, rules: RuleEngine) -> Self {
        self.rules = Some(rules);
        self
    }

    /// Set the actuator (the catalog exposure point).
    #[must_use]
    pub fn actuator(mut self, actuator: A) -> Self {
        self.actuator = Some(actuator);
        self
    }

    /// Build the pipeline.
    ///
    /// # Errors
    ///
    /// Returns the corresponding [`PgsoBuildError`] variant if any stage was
    /// not supplied. This never panics, so a wiring mistake cannot crash the
    /// host process (master spec §5).
    pub fn build(self) -> Result<Pgso<S, A>, PgsoBuildError> {
        Ok(Pgso {
            signal: self.signal.ok_or(PgsoBuildError::MissingSignal)?,
            engine: self.engine.ok_or(PgsoBuildError::MissingEngine)?,
            rules: self.rules.ok_or(PgsoBuildError::MissingRules)?,
            actuator: self.actuator.ok_or(PgsoBuildError::MissingActuator)?,
            audit_log: AuditLog::new(),
        })
    }
}
