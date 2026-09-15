# Reproducible engineering evaluation

This package evaluates the checked-out PGSO implementation using authored
synthetic policy criteria, actual in-process tool callbacks, and a loopback
HTTP/MCP server. It does not estimate emotion-recognition accuracy,
conversational utility, deployment safety, or superiority over another system.

## Execution

Requirements: Git, Python 3.10 or newer, a stable Rust toolchain capable of
building the workspace examples, and an available IPv4 loopback socket.
Cargo downloads the dependencies pinned by the repository's `Cargo.lock` on
the first build. No voice corpus, model weights, external service credentials,
Python packages, or paid API calls are required.

From the repository root:

```bash
python3 experiments/reproducibility/run.py
```

The default is a release build, with a new timestamped output directory under
`target/reproducibility/`. For an explicit output location and cached dependencies:

```bash
python3 experiments/reproducibility/run.py --offline --output /tmp/pgso-evaluation
```

The output directory must not already exist. Existing evidence is never replaced.
Use `--profile dev` for the development build used by CI. Compilation failures,
missing or duplicated cases, failed assertions, and differing fresh-process
replays return a nonzero exit status. Python optimization does not disable the
top-level runner's checks. A partially completed run is marked `failed` in
`results.json`; only a completed evaluation is marked `passed`.

## Evaluation design

| Suite | Frozen fixture | Observation and acceptance criterion |
|---|---|---|
| Governance trace | Eleven scenarios, 454 observations; nine scenarios contain 184 labeled observations | Compare actual pipeline output with independently authored expected states. Check protected-tool presence, the fixture's single directive, and cumulative transition counts at every observation. |
| Execution | Thirteen named cases | Execute the actual `Runtime::call` boundary and inspect cumulative callback counts, return values, and confirmation handling. |
| HTTP/MCP transport | Fourteen named cases | Start the actual example server on a temporary loopback port; check bearer authentication, rejected browser origins, schemas, sessions, tool calls, and JSON-RPC routing. |

The trace configuration is defined in
[`validation_trace.rs`](../../crates/pgso-core/examples/validation_trace.rs).
The scenario segments are recorded in
[`governance-scenarios.json`](governance-scenarios.json). Each scenario starts
with a fresh pipeline. The CSV `expected` column is accepted for annotation but
never enters the decision computation; an example test checks that changing it
does not change an actual triggered trace. CSV records must occupy one line.

The slow-ramp and shifted-baseline scenarios deliberately have no expected
labels. Their restricted-observation counts describe sensitivity; they are not
scored as correct or incorrect. The labeled scenarios encode the specified
temporal contract, so agreement with them cannot establish comparative utility.
The directive-count assertion applies to this particular one-directive fixture,
not to every admissible PGSO rule set.

The execution suite covers permitted and pruned calls, protected-tool step-up,
single-use confirmations, expiry, session isolation, invalid arguments, callback
failure, and a policy-transition sequence. The transition case applies recovery
followed by re-restriction: it demonstrates invalidation across that sequence,
not that recovery alone caused revocation. These are in-process memory-counter
callbacks, not commercial transactions.

The transport suite uses the existing server's `NoAudio` input, empty rule set,
and echo callback. It therefore establishes only the tested protocol behavior;
it does not demonstrate prosodic transitions over HTTP, TLS, concurrent requests,
complete MCP conformance, or interoperability with commercial platforms.

## Outputs and provenance

| File | Content |
|---|---|
| `inputs.csv`, `trace.csv` | Authored observations and actual pipeline output |
| `governance.json` | Per-scenario counts, mismatches, restrictions, and transitions |
| `execution.json`, `http.json` | Named cases, expected/observed outcomes, and pass indicators |
| `results.json` | Completion status and suite summaries |
| `manifest.json` | Git revision and working-tree state; source/input hashes; binary hashes; compiler, Python, and platform details; build command and relevant compiler settings; output hashes |

Two fresh processes must agree for the governance and execution suites on the
same build. This is not evidence of bitwise reproducibility across architectures,
compilers, or compiler settings. A dirty checkout is disclosed rather than
represented as an immutable release. The fixture's `reference_sdk_commit` records
its historical origin; the manifest records the implementation actually built.

The runner builds from source and does not redistribute machine-specific
binaries or copy historical result files. A result should be cited together with
its manifest and complete case outputs. Rerun after any implementation or fixture
change; passing historical outputs are not evidence for a new revision.

### Recorded reference execution

The [reference outputs](reference-results/54c1c7d/results.json) were obtained from
the clean source revision `54c1c7d` on the platform recorded in its
[manifest](reference-results/54c1c7d/manifest.json), using a release build.
The run produced zero mismatches on 184 labeled observations (454 observations
in total), and passed all 13 execution and 14 transport cases. The raw inputs,
trace, and individual case outputs are retained beside the manifest. This is a
bounded engineering result, not an empirical comparison or population estimate.
The subsequent commit adds this record without changing the evaluated source.

## Scope of the remaining research

Separate studies are required for matched comparisons with existing governance
frameworks, natural conversational speech, blinded human judgments, and native
commercial voice systems. Those studies must specify their own independent
targets, shared inputs, unsupported capabilities, speaker/task splits, and
statistical analysis. This finite engineering package is a prerequisite for
those evaluations, not their replacement.

## Matched lifecycle and dispatch comparison

`compare_lifecycle.py` adds a separate, finite comparison of PGSO, NeMo
Guardrails, and Invariant, plus an instantaneous-threshold engineering ablation
and a voice-agnostic control. The two controls are not competing products.
The comparison contains 23 authored scenarios and 96 candidate calls per arm.
Every allowed call executes an in-memory callback; every blocked call must leave
its counter unchanged. Deterministic receipts identify the callback and ordinal. These are source-inspected, in-process instrumentation: their internal consistency is checked, but this is not an independent attestation channel against a malicious replacement executable.

Use separate Python environments with `nemoguardrails==0.24.0` and
`invariant-ai==0.3.5`, respectively. The recorded run uses Python 3.12 for both.
The manifest records all installed distribution versions and hashes the installed
framework source/configuration files. These installed bytes, not an assumed
upstream Git revision, identify the evaluated frameworks. Different dependency
resolutions constitute a new environment and must be reported as such.

```bash
python3 experiments/reproducibility/compare_lifecycle.py \
  --nemo-python /path/to/nemo-environment/bin/python \
  --invariant-python /path/to/invariant-environment/bin/python
```

The runner builds the actual `lifecycle_comparison` Rust example from the
checkout. `--output` requires a new directory; `--profile dev` selects a debug
build. Framework policy evaluation uses no LLM and requires no API credentials.
Framework stderr, including NeMo's expected missing-LLM notices, is retained.

### Exact common engineering contract

- One arousal axis, fixed baseline `0.5`, EMA alpha `0`, no warm-up, inclusive
  absolute deviation `0.3`, inclusive confidence `0.5`, hysteresis of three
  accepted readings, and maximum accepted gap `1200 ms`.
- Inputs are validated in double precision before conversion to IEEE binary32;
  comparator arithmetic uses binary32 at the same observation/deviation boundary.
- Deviation magnitude and direction determine the hysteresis run. A direction
  reversal restarts it. Only a sustained rising run activates the quote rule.
- Pending readings, low-confidence readings, backward timestamps, and silence
  preserve existing policy. Accepted gaps greater than the maximum reset the
  run, then process the new reading. Equal timestamps count as separate readings.
- A nominal accepted observation retires the rule. A sustained falling run also
  retires it; its pending readings preserve the previous policy.
- Explicit `expire` retires a contribution strictly older than `cutoff_ms`.
  Contribution age is its last rule-trigger timestamp, not the last accepted
  reading. Expiry does not reset the engine counter or accepted timestamp.
- A governed quote is blocked; protected human handoff is always callable in
  this fixture. Confirmations, directives, multiple axes, and adaptive baselines
  are outside this common comparison. The original execution suite tests PGSO's
  confirmation handling separately.

The NeMo and Invariant adapters share a host implementation of this temporal
contract. Real Colang and Invariant DSL policies decide the candidate tool call
from host context, then the host invokes the callback only after permission.
Neither adapter consumes PGSO output. This evaluates **framework integrations**,
not independent native implementations of paralinguistic state. The host code
is part of each treatment and must be counted when describing integration cost.
Catalog hiding alone is never scored as successful dispatch prevention.

The threshold engineering ablation removes hysteresis and uses a fixed `0.8`
cutoff, retaining confidence/backward-observation hold and explicit expiry.
Consequently it is not the fully stateless instantaneous arm proposed for the
future human experiment. The voice-agnostic control ignores observations for
its tool decisions. Neither control establishes that voice awareness improves
conversation quality merely by differing from the authored contract.

### Evidence and acceptance

`contract-cases.json` retains authored expected decisions. `episodes.json` and
its reversed copy omit those labels; adapters reject unknown fields, including
injected expectations. The framework output files retain decisions, callback
counts and receipts. `differences.json` reports every mismatch, including
ablations. `results.json` reports allowed and blocked opportunity counts,
missed blocks, unnecessary blocks, and repeat-process agreement. The manifest
hashes source, binary, environment inventory, raw inputs, outputs, and stderr.
Sources and framework inventories are rechecked before a run can pass.

Each arm must reject seven malformed inputs with an explicit validation-error marker. A crash or dependency-import failure does not count as input rejection. The complete payload is validated before any callback executes. PGSO, matched NeMo, and matched
Invariant must satisfy all authored decisions. Ablations may differ; their
mismatches remain visible. A fresh process repeats each arm with reversed
episode order. Repetition is a reproducibility check, not an independent sample.
The finite counts are not estimates of population error rates; no confidence
interval, significance test, latency ranking, or superiority claim is justified.

Run the dependency-free evidence-gate checks with:

```bash
python3 experiments/reproducibility/test_comparison.py
```

The [pilot procedure](pilot-procedure.md) supplies the proposed human-rater
instructions, blinding, data schema, and preregistration gates. Its broader
conversational experiment still requires authorized natural speech, fixed-model
access, and human raters. Passing this engineering comparison does not supply
those observations or approve the confirmatory protocol.

### Recorded lifecycle comparison

The clean release execution at `090fcd8` is preserved in
[reference-results/lifecycle-090fcd8](reference-results/lifecycle-090fcd8/results.json),
including the complete manifest, raw calls, receipts, differences, and stderr.
Each arm processed the same 23 scenarios and 96 calls, repeated in a fresh
process with reversed episode order, and rejected seven malformed payloads.

| Configured arm | Callbacks executed | Calls blocked | Missed authored blocks | Additional authored blocks |
|---|---:|---:|---:|---:|
| PGSO | 68 | 28 | 0 | 0 |
| NeMo + host lifecycle | 68 | 28 | 0 | 0 |
| Invariant + host lifecycle | 68 | 28 | 0 | 0 |
| Threshold engineering ablation | 61 | 35 | 2 | 9 |
| Voice-agnostic control | 96 | 0 | 28 | 0 |

PGSO and the matched integrations agree within this finite contract. This result
provides no evidence of PGSO superiority over either integration. The ablation
counts show disagreement with authored temporal rules, not measured harms or
inferior human experience. Natural-speech and human-preference evidence remains
uncollected by this package.

## Execution diagnostics regression

The HTTP runtime's diagnostic receipts are checked in
[`tests/execution.rs`](../../crates/pgso-actuator-http/tests/execution.rs).
The tests exercise actual authorization and callbacks, assert bounded reasons
for denials/failures, check that receipt serialization omits callback error text
and confirmation tokens, and link same-timestamp calls to the committed policy
history across receipt drains.

```bash
cargo test -p pgso-actuator-http --test execution
```

This is a functional information-availability check. It does not measure human
incident-diagnosis accuracy, developer effort, or comparative auditability.
Those require independent tasks and equivalent competitor instrumentation as
specified in the research protocol. Complete event replay and durable audit
storage remain host responsibilities.
