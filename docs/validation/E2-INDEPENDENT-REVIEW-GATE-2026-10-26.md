# E2 — Blinded review and independent intervention-appropriateness gate (draft)

**Status:** prospective protocol; NOT a researcher-approved study, preregistration, consent authorization or experimental outcome. **Source base:** `06e2432` (`main`, 2026-10-08). No empirical result is changed.

## Why E2 is necessary

The existing SDK proves *conditional software execution contracts*, not that a vocal observation warrants a tool restriction. The Vault records a seven-arm development voice-task pilot (three tasks per arm) with zero successful task predicates in every arm, so task superiority is unresolved. An injected-signal roaming diagnostic withheld a repair that was legitimate once the restriction expired. Both observations motivate measuring false interventions and retained task capacity.

## Two independent judgments, no self-oracle

1. **Permission:** A blinded reviewer applies a fixed task/host policy to a candidate action and frozen pre-action state: `permitted`, `prohibited` or `unresolved`. The existing `export_permission_packets.py` produces policy-hash-checked, condition-blinded attempted-action packets with private linkage. Its tests are added to CI in this branch.
2. **Intervention appropriateness:** A *different* blinded review uses the relevant voice excerpt (only if authorized), dialogue prefix, task and current tool opportunity to rate whether delay/clarification was `required`, `permissible`, `unwarranted` or `unresolved`. The reviewer must not infer psychological state from signal intensity or receive PGSO's output.

**Critical denominator gap:** current permission packets include only **attempted** tool calls. Hiding a tool may stop the agent from attempting a legitimate action. Thus the exporter cannot measure all missed permitted actions; pre-authored task checkpoints and independent opportunity inspection remain mandatory.

## Matched comparisons (freeze before test)

| Arm | Information made available | What it tests |
|---|---|---|
| B0 ordinary agent | Same transcript and host task; no additional acoustic observation | Information-access baseline |
| B1 equally informed contextual adaptation | Same acoustic history and timestamps available to PGSO | Prosodic knowledge without PGSO enforcement |
| P PGSO | Same acoustic observations plus explicit SDK policy state and runtime dispatch | Incremental value/risks of governed action |
| T instantaneous threshold | Same observations; predeclared no-hysteresis rule | Contribution of temporal gating |
| S temporally shuffled observations | Same signal distribution but deliberately misaligned timing | Negative control for correct temporal association |

Framework comparisons with NeMo and Invariant are a separate integration analysis: count any host-authored temporal logic and match actual capabilities, never claim framework superiority from one fixed contract.

## Prospective outcomes

- Independent task success from **actual final sandbox state**, without replaying tool calls that were denied.
- Prohibited calls executed, allowed actions withheld (including unattempted opportunities), unnecessary confirmations, recovery delay and callback effects.
- Audio/ASR/model failures and full-path latency separately from decision-core timing.
- Report per task, speaker and session. Windows, callbacks or tokens are not independent persons/tasks.

## Feasibility gates and decisions owned by researcher

- A task-development set with a nondegenerate success distribution across conditions. The archived 0/3 outcomes are not confirmation data.
- Task policy and thresholds approved **before** seeing confirmatory outcomes; task/speaker splits, model/seed, negative controls and intervention annotation procedure fixed.
- Natural audio corpus authorization and appropriate participant/rater ethics process.
- Primary endpoint and the operational definition of *appropriate intervention* approved by researcher through PhDude. No direct edits to the Vault's knowledge or decision files.
- Review of the actual synthetic CI packet tests does not substitute for human judgments.

## Existing executable preflight

```bash
python3 experiments/public_voice/test_export_permission_packets.py
```

This dependency-free test checks deterministic export, policy-hash integrity, private treatment linkage, omission of future outcomes, reproducibility of blind packets and malformed-input rejection. It uses only synthetic records and does not call model or voice providers.

**Related docs:** `experiments/public_voice/README.md`, `experiments/reproducibility/pilot-procedure.md`, and the Vault `manuscript/methods.md` and `manuscript/results.md`. This proposal supplements, but never overrides, the Charter.
