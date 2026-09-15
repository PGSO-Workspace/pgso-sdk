# Persistent integration-path measurements, SDK7805fb9

Exploratory measurements executed on 15 September 2026. The measured implementation
commit is `7805fb9`; raw manifests contain its full identifier and a clean Git
status. The publication commit adds only reports and archived evidence.

## Design and results

Four implementations completed 12 paired process blocks each, with 100 measured
batches per process: 4,800 measured batches in total. Each batch replays the same
23 authored scenarios and 96 candidate calls with fresh logical sessions. Every
measured response retained the expected decisions and callback receipts: 68 calls
execute and 28 are blocked per batch. All 48 process blocks passed. These are
repetitions of a development contract, not 4,800 independently sampled tasks.

The worker stays alive between batches. Measurements include input parsing,
session construction, temporal policy processing, actual callback dispatch,
response serialization and local pipe transport. No speech extractor, LLM or
network service is included. PGSO uses its Rust runtime; NeMo Guardrails 0.24.0
uses a persistent Colang 1.0 input-rail integration; Invariant 0.3.5 uses a
persistent LocalPolicy integration. The minimal Python reference implements the
same temporal decisions without a framework, and has fewer SDK facilities.

The host is a Linux x86_64 KVM virtual machine reporting AMD EPYC 9R14 and 16
logical CPUs. Workers are pinned to CPU 15 and the parent to CPU 0. There are five
warm-up batches after the separately measured first batch. Controller order is
balanced, scenario order is shared within each block, and the seed is 20260915.
The complete compiler, interpreter, dependency, source, clock and host records
are in the manifest. CPU frequency metadata is unavailable; affinity cannot
remove hypervisor contention. See [protocol](README.md#operational-measurement).

Latency is the median of the 12 process-block p50 batch latencies. Its interval
uses 2,000 process-block bootstrap draws. CPU is the median per-block aggregate
worker scheduler runtime divided by the 100 measured batches; it includes worker
activity during the validation gaps between requests. RSS is a process snapshot,
not incremental allocation. Units in this table refer to a full 96-call batch.

| Integration | Batch latency, ms (95% interval) | Worker CPU, ms/batch | Warm RSS, MiB | Startup through first batch, ms |
|---|---:|---:|---:|---:|
| Minimal Python reference | 0.604799 (0.603863–0.607871) | 0.565171 | 12.311 | 23.938 |
| Invariant / LocalPolicy | 23.086647 (23.023295–23.171516) | 23.224692 | 62.777 | 567.291 |
| NeMo / Colang input rail | 425.849599 (425.373111–427.079734) | 430.362655 | 68.346 | 1068.719 |
| PGSO | 0.515425 (0.512073–0.518055) | 0.477043 | 13.127 | 10.339 |

| Integration | Median block p95, ms | Median block p99, ms | Final RSS, MiB | Lifetime peak RSS, MiB |
|---|---:|---:|---:|---:|
| Minimal Python reference | 0.626031 | 0.642087 | 12.322 | 12.322 |
| Invariant / LocalPolicy | 23.400312 | 25.311175 | 62.959 | 62.959 |
| NeMo / Colang input rail | 451.322712 | 453.010181 | 68.432 | 68.432 |
| PGSO | 0.536782 | 0.556193 | 13.152 | 13.152 |

These p95/p99 summaries are descriptive, not deployment tail-latency guarantees.
Every raw timing, including spikes, is retained. The cold-start measure includes
process launch and the first complete batch; readiness messages occur at different
initialization stages and are not compared as equivalent initialization costs.

| Comparator | Median paired PGSO/comparator latency ratio | Pointwise 95% interval |
|---|---:|---:|
| Minimal Python reference | 0.851699 | 0.846206–0.855259 |
| Invariant / LocalPolicy | 0.022285 | 0.022197–0.022408 |
| NeMo / Colang input rail | 0.001212 | 0.001197–0.001216 |

## Interpretation

PGSO incurred lower batch latency and worker CPU time than the three configured
paths in this measurement. Against the minimal Python reference, the median
paired latency ratio corresponds to approximately 14.8% lower latency. The Python
reference used less warm resident memory (12.311 versus 13.127 MiB). Both findings
must remain visible. This is not evidence that the PGSO algorithm is intrinsically
faster: language, implementation scope, validation, runtime initialization and
framework facilities differ.

The larger gaps against NeMo and Invariant concern their selected public API
paths. NeMo processes a full deterministic conversational event chain for each
candidate call. Read-only installed-source review found no repeated initialization
or model inference in the measured path. Only its exact expected no-main-model
warning is filtered; other stderr is retained. This does not establish performance
of other APIs, vendor-native voice agents or complete commercial products.

The intervals quantify between-process repeatability on one virtual host. The
study does not establish performance across deployments, a practical voice-agent
latency benefit, independent confirmatory superiority, integration productivity,
or conversational utility. The measured workload was developed with the system;
untouched task families and independent hosts are needed before generalizing.
No result here establishes publication readiness or algorithmic novelty.

## Recorded diagnostic information

The separate clean engineering run passed all 184 labeled governance observations
(454 observations in total), 13 execution scenarios and 14 HTTP cases. Two fresh
process runs produced equal normalized governance/execution output. Each of the
13 direct-call cases emitted one receipt and matched a predeclared reason label.
The labels are authored in the validation executable, not independently annotated
by participants. Seven stable fields are retained after excluding duration_us.

The fixture contains three outcome categories and seven reason categories. Its
nine denials span five reasons. Five denials share confirmation_missing: the
receipt alone cannot distinguish initial absence, replay and several revocation
or consumption histories. Full causal diagnosis requires additional host records.
These are information-availability and regression checks, not human diagnostic
accuracy or time-to-resolution results. Competitor diagnostic usability was not
measured and no auditability ranking follows.

Developer integration and maintenance effort remain unmeasured. So do human
incident diagnosis and natural conversational utility. Agent implementation time
and source line counts are not substituted for participant observations. The
existing proposed developer/diagnosis and human-preference protocols remain the
route to these endpoints; this runtime experiment does not replace them.

## Evidence and reproduction

- [Complete operational results and manifest](reference-results/operational-7805fb9/manifest.json).
- [Operational summary](reference-results/operational-7805fb9/results.json).
- [Engineering results and manifest](reference-results/engineering-7805fb9/manifest.json).
- [Actual diagnostic cases](reference-results/engineering-7805fb9/execution.json).
- [Agent review notes](operational-review-7805fb9.md), explicitly distinct from human peer review.
- [Both calibration runs](operational-calibration-7805fb9.tar.gz), excluded from the main estimates.

The first calibration used four blocks with five measured batches and coarse CPU
ticks; the second used four blocks with two batches to check the warning filter
and scheduler accounting. Both used uncommitted development code, preserved in
their manifests, and informed the final protocol. No pilot estimate is reported
as a confirmatory result. Source/interpreter/framework/binary checks passed before
and after the main run. Every output file has a recorded SHA-256 hash.

Reproduce on Linux using the exact environments recorded in the manifest and the
command in the protocol. The raw folders can be verified with
`python3 experiments/reproducibility/test_package.py`. Repeated measurements need
not reproduce identical timings; matching hashes establish retained evidence
integrity, not independent scientific replication.
