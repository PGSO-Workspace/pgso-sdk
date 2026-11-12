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
//! ## Recovery semantics
//!
//! Each valid observation replaces the contributions of its own axis. Nominal
//! observations retire that axis; abstention/pending/silence hold its state.
//! Other axes survive. Readings are processed in input order independently of
//! batch boundaries. The complete resulting policy is reconciled atomically.
//! Trusted hosts may explicitly expire stale policies with `expire_before`.
//!
//! ## No panics (master spec §5)
//!
//! Nothing in this module panics. [`PgsoBuilder::build`] returns a typed
//! [`PgsoBuildError`] rather than unwrapping missing stages, and
//! [`Pgso::process_window`] propagates [`ActuatorError`] with `?`.

use std::collections::BTreeMap;

use crate::{
    action::{Action, AuditRecord, ScopeDecision},
    audit::{AuditLog, TransitionCause},
    engine::{DecisionEngine, ProcessOutcome},
    error::ActuatorError,
    rules::RuleEngine,
    traits::{Actuator, Signal},
    types::{AudioWindow, Axis, Catalog, SignalReading},
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
    /// Engine, rule, or catalog configuration is invalid.
    #[error(transparent)]
    InvalidConfig(#[from] crate::ConfigError),
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

/// Provenance of one active contribution (one axis and its matched rules), for audit only.
///
/// `activated` is the reading that made the contribution, with these rule ids,
/// active; a reading that changes the matched rules starts a new one. `renewed` is
/// the latest reading that re-triggered it. Expiry compares `renewed`'s timestamp
/// with the cutoff. Recording this never changes a governance decision.
#[derive(Debug, Clone)]
pub struct ContributionProvenance {
    /// Axis whose contribution this is.
    pub axis: Axis,
    /// Reading that activated the contribution.
    pub activated: SignalReading,
    /// Latest reading that renewed it; equals `activated` until renewed.
    pub renewed: SignalReading,
    /// Rule identifiers of the current decisions.
    pub rule_ids: Vec<String>,
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
    active: BTreeMap<Axis, Vec<ScopeDecision>>,
    provenance: BTreeMap<Axis, ContributionProvenance>,
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

        for reading in &readings {
            // Stage the engine as well as policy: failed actuator commits must
            // not consume a reading or advance hysteresis/baselines.
            let mut engine = self.engine.clone();
            let mut next = self.active.clone();
            let cause = match engine.process_outcome(reading) {
                ProcessOutcome::Abstained | ProcessOutcome::Pending => {
                    self.engine = engine;
                    continue;
                }
                ProcessOutcome::Nominal => {
                    next.remove(&reading.axis);
                    TransitionCause::NominalReading
                }
                ProcessOutcome::Triggered(output) => {
                    let decisions = self.rules.evaluate(&output);
                    if decisions.is_empty() {
                        next.remove(&reading.axis);
                        TransitionCause::NoMatchingRule
                    } else {
                        let cause = if self.active.contains_key(&reading.axis) {
                            TransitionCause::Replacement
                        } else {
                            TransitionCause::Activation
                        };
                        next.insert(reading.axis, decisions);
                        cause
                    }
                }
            };
            self.commit_policy(
                next,
                reading.timestamp_ms,
                Some(reading.clone()),
                None,
                cause,
            )?;
            self.engine = engine;
            self.record_provenance(reading);
        }

        Ok(self.actuator.current_catalog())
    }

    fn commit_policy(
        &mut self,
        next: BTreeMap<Axis, Vec<ScopeDecision>>,
        timestamp_ms: u64,
        reading: Option<SignalReading>,
        expiry_cutoff_ms: Option<u64>,
        cause: TransitionCause,
    ) -> Result<(), ActuatorError> {
        let active: Vec<_> = next.values().flatten().cloned().collect();
        let before = self.actuator.current_state();
        self.actuator.reconcile(&active)?;
        let after = self.actuator.current_state();
        if before != after {
            if active.is_empty() {
                self.audit_log.record(&ScopeDecision::with_audit(
                    Action::Allow,
                    AuditRecord::restore(timestamp_ms),
                ));
            } else {
                for decision in &active {
                    self.audit_log.record(decision);
                }
            }
            self.audit_log.transition(crate::audit::PolicyTransition {
                timestamp_ms,
                expiry_cutoff_ms,
                reading,
                before,
                after,
                active,
                cause,
            });
        }
        self.active = next;
        Ok(())
    }

    /// Retire contributions older than the trusted host's cutoff. Silence alone
    /// never grants permissions. `timestamp_ms` is the trusted host's current
    /// time, distinct from the evidence-age cutoff. Call under the dispatch lock.
    ///
    /// # Errors
    /// Leaves policy unchanged if the cutoff is in the future or reconciliation fails.
    pub fn expire_before(
        &mut self,
        cutoff_ms: u64,
        timestamp_ms: u64,
    ) -> Result<Catalog, ActuatorError> {
        if cutoff_ms > timestamp_ms {
            return Err(ActuatorError::Internal(
                "expiry cutoff exceeds host time".into(),
            ));
        }
        let mut next = self.active.clone();
        next.retain(|_, decisions| decisions.iter().any(|d| d.audit.timestamp_ms >= cutoff_ms));
        self.commit_policy(
            next,
            timestamp_ms,
            None,
            Some(cutoff_ms),
            TransitionCause::HostExpiry,
        )?;
        let active = &self.active;
        self.provenance.retain(|axis, _| active.contains_key(axis));
        Ok(self.current_catalog())
    }

    /// Update provenance after a committed reading. Audit-only bookkeeping.
    /// Called only after a reading committed; a triggering reading always carries
    /// the axis' current decisions. A changed rule set starts a new contribution.
    fn record_provenance(&mut self, reading: &SignalReading) {
        let Some(decisions) = self.active.get(&reading.axis) else {
            self.provenance.remove(&reading.axis);
            return;
        };
        let rule_ids: Vec<String> = decisions
            .iter()
            .filter_map(|d| d.audit.rule_id.clone())
            .collect();
        match self.provenance.get_mut(&reading.axis) {
            Some(entry) if entry.rule_ids == rule_ids => entry.renewed = reading.clone(),
            _ => {
                self.provenance.insert(
                    reading.axis,
                    ContributionProvenance {
                        axis: reading.axis,
                        activated: reading.clone(),
                        renewed: reading.clone(),
                        rule_ids,
                    },
                );
            }
        }
    }

    /// Active contributions with their activating and latest renewing readings.
    pub fn contributions(&self) -> impl Iterator<Item = &ContributionProvenance> {
        self.provenance.values()
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

    /// Borrow the underlying actuator.
    ///
    /// Read-only access to the exposure point so callers (and end-to-end tests)
    /// can inspect actuator-specific state the [`Actuator`] trait does not
    /// surface — e.g. the injected directive blocks that G1 requires be cleared
    /// when the catalog is restored to nominal.
    #[must_use]
    pub const fn actuator(&self) -> &A {
        &self.actuator
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
        Self {
            signal: None,
            engine: None,
            rules: None,
            actuator: None,
        }
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
    /// The actuator must expose its initial catalog, with unique nonblank IDs.
    /// Rule actions and protected IDs must refer to tools in that catalog.
    ///
    /// # Errors
    ///
    /// Returns the corresponding [`PgsoBuildError`] variant if any stage was
    /// not supplied, or configuration references are invalid. This never panics,
    /// so a wiring mistake cannot crash the host process (master spec §5).
    pub fn build(self) -> Result<Pgso<S, A>, PgsoBuildError> {
        self.engine
            .as_ref()
            .ok_or(PgsoBuildError::MissingEngine)?
            .validate()?;
        self.rules
            .as_ref()
            .ok_or(PgsoBuildError::MissingRules)?
            .validate()?;
        self.rules
            .as_ref()
            .ok_or(PgsoBuildError::MissingRules)?
            .validate_catalog(
                &self
                    .actuator
                    .as_ref()
                    .ok_or(PgsoBuildError::MissingActuator)?
                    .current_catalog(),
            )?;
        Ok(Pgso {
            signal: self.signal.ok_or(PgsoBuildError::MissingSignal)?,
            engine: self.engine.ok_or(PgsoBuildError::MissingEngine)?,
            rules: self.rules.ok_or(PgsoBuildError::MissingRules)?,
            actuator: self.actuator.ok_or(PgsoBuildError::MissingActuator)?,
            audit_log: AuditLog::new(),
            active: BTreeMap::new(),
            provenance: BTreeMap::new(),
        })
    }
}
