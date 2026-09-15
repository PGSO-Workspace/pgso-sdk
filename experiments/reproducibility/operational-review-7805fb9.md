# Measurement review notes

These are agent review observations, not independent human peer review.

## Installed NeMo path

Read-only inspection of installed nemoguardrails 0.24.0 identified a persistent
LLMRails instance with Colang 1.0 input-rail policy and no language model. Each
candidate call traverses the public conversational event pipeline. The reviewer
observed approximately 4.48 ms per call in an exploratory local check; this is
calibration context only and is not included in the main measurement. A public
generate_events variant did not materially remove that event-chain cost.
The selected result therefore concerns the configured deterministic input-rail
integration, not all NeMo policies, product versions, or raw policy-engine speed.
The installed framework sources and configuration are fingerprinted in the main
manifest. The expected missing-model warning alone is filtered in all runs of
that worker; other logs are preserved.

## Diagnostic interpretation

The 13 existing direct-call scenarios each drain exactly one actual execution
receipt and check a predeclared case-name-to-reason label. Those labels are
separate from the emitted value but colocated in the validation executable;
they are not independent annotations by participants. Seven stable serialized
fields remain after omitting duration_us: outcome, reason, session, tool,
timestamp_ms, revision, and policy_transition_count. This does not imply seven
independent signals or complete causal reconstruction.

Three outcome categories and seven reason categories occur in this fixture.
The nine denials span five reason categories. Five denials share
confirmation_missing, which does not distinguish initial absence, replay, or
revocation without separately retained event history. Two fresh process runs
produce equal normalized JSON. No human diagnostic time or accuracy was measured.

## Analysis limits

This is exploratory discovery on one virtual host and a development contract.
Repeated batches are not independent tasks, users, deployments, or replication
hosts. Bootstrap units are paired process blocks. Tail percentiles are
descriptive, and no slow observation is discarded. The minimal Python reference
must remain visible even on metrics where it outperforms PGSO. Operational speed
alone does not establish algorithmic novelty, conversation quality, integration
productivity, or submission readiness.
