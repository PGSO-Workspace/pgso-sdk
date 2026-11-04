# Execution context and transition cause (contract)

Branch `exp/sdk-api-audit-20261104`, on top of the provenance candidate `050da93` (main `06e2432`).
Both additions are **observational**: no engine decision, rule evaluation, catalog reconciliation or
authorization check changes.

## `PolicyTransition::cause` (`pgso-core`)

Every committed transition now carries a `TransitionCause`, assigned where the event is processed:

| Cause | Where it is set | Event |
|---|---|---|
| `Activation` | `Pgso::process_window`, `ProcessOutcome::Triggered` with matching rules, axis had no contributions | triggering reading |
| `Replacement` | same, axis already had contributions and the matched set changed | triggering reading |
| `NominalReading` | `ProcessOutcome::Nominal` | the nominal reading |
| `NoMatchingRule` | `ProcessOutcome::Triggered` with no matching rule (e.g. falling run under a rising-only rule) | the triggering reading |
| `HostExpiry` | `Pgso::expire_before` | trusted host expiry (`expiry_cutoff_ms` set, `reading` None) |

Renewals (same rule set) change no state and therefore create no transition, as before.
`TransitionCause::as_str()` gives stable identifiers (`activation`, `replacement`, `nominal_reading`,
`no_matching_rule`, `host_expiry`); `is_retirement()` is true for the last three.
The enum is `#[non_exhaustive]`.

## `Runtime::execution_context()` (`pgso-actuator-http`)

Read-only (`&self`): it does not observe time, consume confirmations or mutate anything. Returns
`ExecutionContext` (serde-serializable):

| Field | Meaning |
|---|---|
| `schema`, `schema_version` | `"pgso.execution_context"`, `1` |
| `session` | authenticated runtime session |
| `observed_at_ms` | latest trusted host time this runtime observed (`None` before any) |
| `authorization_revision` | the revision checked at dispatch; advances on policy transitions, permission changes and observed clock rollbacks (all revoke confirmations) |
| `policy_transition_count`, `last_transition_cause` | audit position and latest cause |
| `tools[]` | `{name, confirmation_required}` for tools exposed by policy **and** host permissions, catalog order (same set as `tools_list`) |
| `directives[]` | active directive blocks, verbatim |
| `pending_confirmations[]` | `{tool, arguments, expires_at_ms}` that dispatch would still accept (current revision, not expired at `observed_at_ms`), ordered by expiry; **no tokens** |

It is **never an authorization**. `Runtime::call` still checks clock, session, token consumption,
permissions, policy, schema and the confirmation's tool/arguments/revision/expiry at dispatch.

## Compatibility

- Additive APIs and types; existing methods unchanged.
- `PolicyTransition` gains a public field. Code that only reads transitions is unaffected; code that
  builds `PolicyTransition` with a struct literal (none known: the constructor is crate-private) must
  add `cause`.
- MSRV unchanged (uses `Option::is_none_or`, already used by the harness; MSRV 1.83).

## Tests

`crates/pgso-core/tests/transition_cause.rs` (6) and two tests in
`crates/pgso-actuator-http/tests/execution.rs`. Workspace: 122 passed (114 before), clippy `-D warnings` clean.
