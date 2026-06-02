//! The [`RuleEngine`]: deterministic mapping from [`EngineOutput`] to a list of
//! [`ScopeDecision`]s, with the inviolable-allowlist guarantee built in.
//!
//! Rules are declared with the [`pgso_rules!`] macro. Each rule has a
//! [`RulePredicate`] (axis + minimum deviation + minimum confidence) and a list
//! of [`Action`]s to emit when it matches.
//!
//! ## Guarantees enforced here
//! - **G2 (inviolable allowlist) + G3 (non-punitive default):** an
//!   [`Action::Prune`] whose target is in the protected set is downgraded to
//!   [`Action::RequireStepUp`] — never emitted as `Prune`, never silently
//!   dropped.
//! - **REQ-2.11 (auditability):** every emitted [`ScopeDecision`] carries a
//!   fully populated [`AuditRecord`].

use crate::{
    action::{Action, AuditRecord, ScopeDecision},
    engine::EngineOutput,
    types::{Axis, ToolId},
};
use std::collections::HashSet;

/// The match condition of a [`Rule`]: an axis plus minimum deviation and
/// confidence thresholds. All three must hold for the rule to fire.
#[derive(Debug, Clone)]
pub struct RulePredicate {
    /// The axis this rule applies to.
    pub axis: Axis,
    /// Minimum deviation (inclusive) required to match.
    pub min_deviation: f32,
    /// Minimum confidence (inclusive) required to match.
    pub min_confidence: f32,
}

impl RulePredicate {
    /// True iff the axis matches and both deviation and confidence meet their
    /// minimums.
    #[must_use]
    pub fn matches(&self, axis: Axis, deviation: f32, confidence: f32) -> bool {
        self.axis == axis && deviation >= self.min_deviation && confidence >= self.min_confidence
    }
}

/// A single governance rule: an id, a [`RulePredicate`], and the [`Action`]s to
/// emit when the predicate matches.
#[derive(Debug, Clone)]
pub struct Rule {
    /// Stable identifier, recorded in the [`AuditRecord`] of emitted decisions.
    pub id: String,
    /// The condition under which this rule fires.
    pub predicate: RulePredicate,
    /// The actions emitted (in order) when the rule matches.
    pub actions: Vec<Action>,
}

impl Rule {
    /// Construct a rule from its parts. Used by the [`pgso_rules!`] macro and in
    /// tests/property generators.
    #[must_use]
    pub fn new(
        id: &str,
        axis: Axis,
        min_deviation: f32,
        min_confidence: f32,
        actions: Vec<Action>,
    ) -> Self {
        Self {
            id: id.to_string(),
            predicate: RulePredicate { axis, min_deviation, min_confidence },
            actions,
        }
    }
}

/// An ordered collection of [`Rule`]s evaluated against an [`EngineOutput`].
#[derive(Debug, Clone)]
pub struct RuleSet {
    rules: Vec<Rule>,
}

impl RuleSet {
    /// Create a rule set from a list of rules.
    #[must_use]
    pub fn new(rules: Vec<Rule>) -> Self {
        Self { rules }
    }

    /// Borrow the contained rules.
    #[must_use]
    pub fn rules(&self) -> &[Rule] {
        &self.rules
    }

    /// Collect, in declaration order, the rules whose predicate matches the
    /// given axis/deviation/confidence.
    fn matching_rules(&self, axis: Axis, deviation: f32, confidence: f32) -> Vec<&Rule> {
        self.rules
            .iter()
            .filter(|r| r.predicate.matches(axis, deviation, confidence))
            .collect()
    }
}

/// Evaluates a [`RuleSet`] against [`EngineOutput`]s and enforces the protected
/// allowlist (G2/G3).
pub struct RuleEngine {
    rules: RuleSet,
    protected: HashSet<ToolId>,
}

impl RuleEngine {
    /// Construct an engine from a rule set and the set of protected tool ids.
    /// Protected ids can never be pruned by [`RuleEngine::evaluate`].
    #[must_use]
    pub fn new(rules: RuleSet, protected: HashSet<ToolId>) -> Self {
        Self { rules, protected }
    }

    /// Evaluate all matching rules and return the resulting decisions.
    ///
    /// G2 + G3: any [`Action::Prune`] targeting a protected tool is downgraded
    /// to [`Action::RequireStepUp`] for the same tool — it is never emitted as a
    /// `Prune` and never dropped. Every returned [`ScopeDecision`] carries a
    /// fully populated [`AuditRecord`] (REQ-2.11).
    #[must_use]
    pub fn evaluate(&self, output: &EngineOutput) -> Vec<ScopeDecision> {
        let matching = self
            .rules
            .matching_rules(output.axis, output.deviation, output.confidence);
        let mut decisions = Vec::new();

        for rule in matching {
            for action in &rule.actions {
                let final_action = match action {
                    // G2 + G3: downgrade — never prune a protected tool, but
                    // never silently drop the intent either.
                    Action::Prune(id) if self.protected.contains(id) => {
                        Action::RequireStepUp(id.clone())
                    }
                    // `RuleEngine` lives in the same crate as `Action`, so the
                    // remaining variants (Allow, RequireStepUp, Prune of an
                    // unprotected tool, InjectDirective, and any future
                    // `#[non_exhaustive]` variant) are passed through unchanged.
                    other => other.clone(),
                };

                decisions.push(ScopeDecision::with_audit(
                    final_action,
                    AuditRecord {
                        timestamp_ms: output.timestamp_ms,
                        signal_value: Some(output.raw_value),
                        axis: Some(output.axis),
                        deviation: Some(output.deviation),
                        threshold_crossed: Some(rule.predicate.min_deviation),
                        rule_id: Some(rule.id.clone()),
                    },
                ));
            }
        }
        decisions
    }
}

/// Declarative macro for constructing a [`RuleSet`].
///
/// Each `rule` block names the rule, its axis, the minimum deviation and
/// confidence to match, and one or more `action:` entries.
///
/// ```
/// use pgso_core::{pgso_rules, Action, Axis, ToolId};
///
/// let rules = pgso_rules! {
///     rule "tense_close" {
///         axis: Arousal,
///         deviation: 0.7,
///         confidence: 0.6,
///         action: Action::RequireStepUp(ToolId::from("close_sale"))
///     };
///     rule "negative_valence" {
///         axis: Valence,
///         deviation: 0.5,
///         confidence: 0.5,
///         action: Action::Prune(ToolId::from("close_sale")),
///         action: Action::InjectDirective("De-escalate.".into())
///     }
/// };
/// assert_eq!(rules.rules().len(), 2);
/// ```
#[macro_export]
macro_rules! pgso_rules {
    ( $( rule $id:literal {
        axis: $axis:ident,
        deviation: $dev:expr,
        confidence: $conf:expr,
        $( action: $action:expr ),+ $(,)?
    } );* $(;)? ) => {
        $crate::RuleSet::new(vec![
            $(
                $crate::Rule::new(
                    $id,
                    $crate::Axis::$axis,
                    $dev,
                    $conf,
                    vec![ $( $action ),+ ],
                )
            ),*
        ])
    };
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pgso_rules;
    use crate::{Action, Axis, ToolId};
    use std::collections::HashSet;

    fn sample_output(axis: Axis, deviation: f32, confidence: f32) -> EngineOutput {
        EngineOutput {
            axis, raw_value: 0.9, deviation, confidence,
            baseline: 0.5, timestamp_ms: 1000,
        }
    }

    #[test]
    fn test_rule_matches_and_emits_action() {
        let rules = pgso_rules! {
            rule "high_arousal" {
                axis: Arousal,
                deviation: 0.4,
                confidence: 0.6,
                action: Action::RequireStepUp(ToolId::from("close_sale"))
            }
        };
        let engine = RuleEngine::new(rules, HashSet::new());
        let decisions = engine.evaluate(&sample_output(Axis::Arousal, 0.5, 0.8));
        assert_eq!(decisions.len(), 1);
        assert_eq!(decisions[0].action, Action::RequireStepUp(ToolId::from("close_sale")));
    }

    #[test]
    fn test_no_match_returns_empty() {
        let rules = pgso_rules! {
            rule "high_arousal" {
                axis: Arousal,
                deviation: 0.9,
                confidence: 0.9,
                action: Action::RequireStepUp(ToolId::from("close_sale"))
            }
        };
        let engine = RuleEngine::new(rules, HashSet::new());
        let decisions = engine.evaluate(&sample_output(Axis::Arousal, 0.3, 0.5));
        assert!(decisions.is_empty());
    }

    #[test]
    fn test_prune_protected_downgraded_to_stepup() {
        // G2+G3: Prune on protected tool → RequireStepUp
        let protected = HashSet::from([ToolId::from("escalate")]);
        let rules = pgso_rules! {
            rule "prune_escalate" {
                axis: Arousal,
                deviation: 0.3,
                confidence: 0.5,
                action: Action::Prune(ToolId::from("escalate"))
            }
        };
        let engine = RuleEngine::new(rules, protected);
        let decisions = engine.evaluate(&sample_output(Axis::Arousal, 0.5, 0.8));
        assert_eq!(decisions.len(), 1);
        assert_eq!(
            decisions[0].action,
            Action::RequireStepUp(ToolId::from("escalate"))
        );
    }

    #[test]
    fn test_default_action_is_stepup() {
        // G3: a rule without Prune → RequireStepUp
        let rules = pgso_rules! {
            rule "stepup_only" {
                axis: Valence,
                deviation: 0.3,
                confidence: 0.5,
                action: Action::RequireStepUp(ToolId::from("close_sale"))
            }
        };
        let engine = RuleEngine::new(rules, HashSet::new());
        let decisions = engine.evaluate(&sample_output(Axis::Valence, 0.5, 0.8));
        assert_eq!(decisions[0].action, Action::RequireStepUp(ToolId::from("close_sale")));
    }

    #[test]
    fn test_audit_record_populated() {
        let rules = pgso_rules! {
            rule "test_rule" {
                axis: Arousal,
                deviation: 0.3,
                confidence: 0.5,
                action: Action::RequireStepUp(ToolId::from("search"))
            }
        };
        let engine = RuleEngine::new(rules, HashSet::new());
        let decisions = engine.evaluate(&sample_output(Axis::Arousal, 0.5, 0.8));
        let audit = &decisions[0].audit;
        assert_eq!(audit.axis, Some(Axis::Arousal));
        assert_eq!(audit.rule_id.as_deref(), Some("test_rule"));
        assert!(audit.deviation.is_some());
        assert!(audit.threshold_crossed.is_some());
        assert_eq!(audit.timestamp_ms, 1000);
    }
}
