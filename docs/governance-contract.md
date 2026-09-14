# Sustained-governance contract and validation boundary

This specifies the reference implementation's corrected behavior. It is not an
approved scientific result or a claim of universal correctness. The independent
expected sequence in `governance_regressions` is written from this contract, not
computed by running a second copy of PGSO as an oracle.

| Input on one axis | Baseline | Hysteresis | Effective policy |
|---|---|---|---|
| Invalid, low confidence or backward timestamp | Hold | Hold | Hold |
| Valid deviation below engine threshold | Update using nominal observation | Reset | Retire that axis's contributions |
| Valid deviation at/above threshold | Freeze, including during pending | Count same-side observations | Hold while pending; reconcile matching rules when triggered |
| Deviation changes side | Freeze | Restart at one | Hold while pending; then reconcile |
| Gap exceeds configured maximum | Hold until processing new observation | Reset before processing | No permission restoration merely from gap |
| No readings / silence | Hold | No automatic clock advancement | Hold until explicit host expiry or new evidence |
| Host expires stale contributions | Unchanged | Unchanged | Reconcile remaining contributions and audit effective change |
| Actuator rejects reconciliation | No engine commit for that reading | No engine commit | Actuator must leave served state unchanged |

The maximum accepted-reading gap is configured with
`DecisionEngine::with_max_gap_ms`. None preserves count-based legacy behavior.
A gap equal to the configured maximum is accepted. Hosts must select a limit
appropriate to the sampling cadence. `expire_before` is a separate policy API;
it can restore nominal exposure but cannot override hard host permissions.

Nominal means below the engine's numerical deviation threshold; it does not mean
that a person is calm. Only nominal readings contribute to baseline warm-up/EMA.
A sustained outlying level is deliberately not treated as a new normal. Initial
reference selection therefore matters: a poorly chosen prior may require a new
engine with an independently calibrated prior. This patch does not provide a
trusted calibration dataset or automatic speaker-change detection.

The absolute magnitude remains the engine gate; rule direction explicitly selects
rising, falling or either. Opposite-side readings cannot complete the same
hysteresis run. Engine thresholds and rule thresholds are separate gates and both
must be reported. Rule `AuditRecord.requested_action` preserves the action before
protected-tool enforcement; `ScopeDecision.action` is the effective action.
Custom AuditRecord struct literals must supply the new optional field.

## Executable checks

```sh
PROPTEST_RNG_SEED=20260914 cargo test --workspace
PROPTEST_RNG_SEED=20260914 cargo test --workspace --release
cargo clippy --workspace --all-targets -- -D warnings
cargo fmt --all -- --check
RUSTDOCFLAGS='-D warnings' cargo doc --workspace --no-deps
cargo +1.83.0 build --workspace --locked
```

The sustained-level regression checks every served catalog: five nominal inputs,
34 elevated inputs, then nominal recovery. The expected restriction starts on the
third elevated observation and persists for the remaining 32 elevated observations.
Exactly two effective transitions are expected (activation and recovery).
The protected-catalog property executes 512 generated cases, each with an explicit
adversarial prune attempt; every served catalog must contain the protected tool.
This is sampled property testing, not an inductive proof or a mutation score.
The completed mutation and four-build trace checks are recorded in
[the verification ledger](validation/2026-09-14-verification.json). The protected
catalog has a separate [conditional inductive argument](protected-catalog-argument.md).
Same-host trace agreement is not cross-platform bitwise determinism evidence.

## Still required before paper-level validation

Slow ramps may remain below the moving threshold: freezing outlying observations
does not solve cumulative change detection. Baseline choice, timestamp gap and
expiry policies require development-only selection and sensitivity analysis.
Failed commits surface as errors but are not persisted as policy transitions;
the host must record failed attempts separately. Mutation testing beyond the
engine/rules scope, cross-platform traces, independent experimental manifests and recovery of
historical paper result artifacts remain outstanding. No historical experiment
or numerical result has been rerun or relabeled by this patch.
