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
