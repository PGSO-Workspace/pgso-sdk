# Conditional protected-catalog invariant

This is a reasoning argument about the reference LocalActuator and McpActuator,
not a proof about arbitrary Actuator implementations, external tool services,
the perception model, or every property attributed to PGSO in a manuscript.

Let C0 be the nominal catalog and P the fixed protected set. Assume P is a subset
of C0, and that all state changes use the reference implementation's methods.
Let Ck be the catalog returned by current_catalog after k successful operations.
Then P is a subset of Ck for every finite k.

**Base case:** construction clones C0, so the assumed inclusion holds at k=0.

**Direct-action step:** Allow clones C0. Prune(x) removes x only if x is not in P.
RequireStepUp changes a flag without deleting a tool. InjectDirective changes only
the directive collection. Each possible action therefore preserves inclusion.

**Reconciliation step:** GovernanceState::reconcile starts with C0 and folds the
active actions using the same guarded operations, ignoring Allow contributions.
The direct-action argument applies inductively to that finite fold. It follows
that its result preserves inclusion regardless of action order or conflicting
rules. The reference actuators commit that complete result. Nominal recovery and
expiry are reconciliations and therefore preserve the same invariant.

By induction on successful operations, the inclusion holds. RuleEngine's separate
conversion of Prune(protected) to RequireStepUp supplies an additional safeguard;
the catalog argument does not depend on that conversion being the only defense.

## What this does not establish

- Constructor validation does not establish P subset C0: the integrator must
  satisfy it. An identifier absent from the nominal catalog cannot be preserved.
- HTTP discovery also filters host permissions. This argument never grants an
  unauthorized user access to a protected tool.
- Availability is not execution authorization. The dispatcher checks session,
  permissions, current exposure, arguments and confirmation independently.
- This does not prove that a directive is appropriate, that an LLM follows it,
  or that the full system has no security vulnerabilities.
- Floating-point baselines and temporal recovery are separate contracts.

The 512-case protected-catalog property tests the served catalog after each
observation and forces an adversarial prune attempt in every generated case.
That executable evidence complements this argument; finite tests are not its
inductive step. Cross-target and mutation checks support different claims.
