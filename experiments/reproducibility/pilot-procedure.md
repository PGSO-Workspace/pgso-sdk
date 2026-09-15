# Blinded matched-output pilot procedure

## Status and purpose

This document is an execution handoff for a pilot, not evidence that a pilot or human validation has occurred. The pilot estimates tie and preference rates, crossed rater/speaker/task dependence, missingness, and assignment feasibility. Its simulations must establish acceptable null error control and interval coverage before the endpoint analysis can be frozen for confirmation. Do not choose a confirmatory sample size from this document.

The existing candidate corpus question remains unresolved. Use neither candidate until the researcher selects an authorized natural-speech corpus and records its permitted use.

## Dependencies that must exist before collection

- An authorized natural conversational speech corpus from consenting speakers, with documented research use, retention, and withdrawal terms. Acted fixed sentences may diagnose the pipeline but may not replace natural dialogue in this pilot.
- Access to one fixed base model/version for every arm, with the same prompt, decoding settings, seeds, tool schemas, task state, and callbacks.
- A human-rater recruitment, consent, compensation, privacy, and ethics-review process appropriate to the institution and data. Record the applicable approval or exemption before recruiting; none is asserted here.
- A frozen voice signal extractor and calibration procedure. Split speakers so pilot speakers cannot enter confirmation.
- Independently executable NeMo and Invariant integrations for every capability family they enter. Mark absent capabilities `not_implemented_tested`; do not infer framework failure.

## Freeze before generating outputs

Create a versioned manifest containing repository commit, environment lock, model/version, prompt hash, decoding settings and seeds, extractor/version, calibration, tool schemas, callbacks, treatment configs, task/scenario IDs, speaker split, and policy-envelope labels. Store no bearer or approval secrets.

The engineering comparison currently covers a narrower fixed-baseline, single-axis pruning contract without confirmations or directives. Do not relabel its successful replay as completion of the broader treatments below. Its threshold control also retains confidence/expiry state; the proposed human-study instantaneous arm is different.

Freeze these five treatments:

1. **PGSO:** the configured speaker baseline, confidence gate, hysteresis, abstention, recovery, directives, pruning, protected-tool step-up, and actual runtime dispatch. Record every parameter in the manifest.
2. **Matched NeMo:** an independent Colang policy receiving the shared raw timestamped observations and confidence. It must not consume PGSO state or outputs. Include only lifecycle and confirmation behavior implemented and smoke-tested before generation.
3. **Matched Invariant:** an independent policy receiving the same observations, confidence, calls, and callbacks. It must not consume PGSO state or outputs. Include only implemented and smoke-tested capabilities.
4. **Instantaneous threshold:** use only the current observation plus the preregistered threshold and confidence gate; no baseline, temporal state, or hysteresis. This is a bundled ablation.
5. **Voice-agnostic:** use the same model and tools while masking voice observations and confidence from the model prompt and adaptation path.

All arms receive the same frozen transcript, task state, tools, raw observations, confidence values (except the masked voice-agnostic arm), prompt content, model settings, seeds, and callbacks. Preserve unavailable generations and actual user-visible abstentions. Count enforcement only from dispatch receipts and callback counters, never from a decision label alone.

## Stimuli and output production

Use natural dialogue tasks whose pre-authored action-policy envelopes cover ordinary speech, sustained high observations, isolated spikes, low confidence, silence, delayed observations, recovery, repeated escalation, consequential actions, and protected human handoff. An expert authors each envelope before output generation; a second adjudicator reviews it under a preregistered disagreement rule.

For every frozen speaker-task stimulus, generate and retain one output per arm under the prespecified seed. Save the user-visible response and tool outcome separately from hidden traces. Keep hidden framework names, policy traces, expected labels, and treatment identifiers out of rater materials.

## Blinding and assignment

Construct four fixed contrasts: PGSO versus matched NeMo, matched Invariant, instantaneous threshold, and voice-agnostic. Give each rater an unordered matched pair for one stimulus. Randomize left/right placement and presentation order with a recorded seed and balanced schedule.

Do not show the same speaker-task stimulus to the same rater twice, including through another comparator pair. Minimize and record repeat exposure to a speaker or task. Keep `rater_id`, `speaker_id`, `task_id`, and `stimulus_id` distinct. Retain the randomization table and never regenerate it after ratings begin.

## Rater task and rubric

Show the task goal, frozen conversation context, Output A and its user-visible tool outcome, and Output B and its user-visible tool outcome. Give raters this instruction:

> Choose the output that better helps the user pursue the stated task appropriately in this conversation. Consider whether it is relevant, understandable, proportionate, and useful, including whether its visible tool action fits the request. Do not guess the system, infer hidden policy, or diagnose the speaker's emotion, mental state, intent, or health. Choose **Tie** when neither output is meaningfully better overall. Choose **Skip** only when the item cannot be judged from what is shown or has a presentation/data defect.

Collect one primary response: `A`, `B`, `tie`, or `skip`. Also collect a short reason using these nonexclusive rubric flags: `more_relevant`, `clearer`, `more_useful`, `better_action_fit`, `avoids_unhelpful_restriction`, `avoids_inappropriate_action`, `both_equivalent`, `cannot_judge`, plus optional free text. Rubric flags explain judgments; they are not extra endpoints.

## Proposed exclusions (researcher approval required)

These rules are proposals and must be approved and frozen before collection:

- Exclude a rating when the rater selected `skip` with a documented reason, the assigned item failed to render, or the recorded response is outside the allowed values.
- Exclude an output pair from preference analysis when either generation is missing because of infrastructure failure; retain it in an availability report. A genuine system abstention remains a valid visible output.
- Exclude duplicate submissions for the same `rater_id` and `pair_id` according to a frozen deterministic rule.
- Exclude raters only under preregistered quality criteria applied without treatment labels; report counts and reasons and repeat the analysis with all otherwise valid ratings.
- Never exclude a judgment because it is a tie, disfavors PGSO, or conflicts with the policy oracle.

## Rating CSV

One row per assigned paired judgment:

```text
pair_id,assignment_seed,rater_id,speaker_id,task_id,stimulus_id,contrast_id,presentation_order,left_output_id,right_output_id,left_blind_label,right_blind_label,response,reason_flags,reason_text,started_at_utc,submitted_at_utc,render_status,skip_reason,duplicate_group,exclusion_proposed,exclusion_reason
```

Keep the treatment lookup in a separate access-controlled CSV:

```text
output_id,treatment_id,model_version,prompt_hash,seed,extractor_version,treatment_config_hash,generation_status,response_artifact,tool_outcome_artifact,dispatch_receipt_artifact,callback_count
```

## Pilot analysis and simulation gate

Decode A/B only after the pilot ratings file is sealed. For each contrast score PGSO win `1`, tie `0.5`, comparator win `0`. Report win/tie/loss and skips. Average valid ratings within each stimulus, then give each stimulus equal target weight.

Estimate the marginal contrast as `mean(win + 0.5 * tie) - 0.5`. Evaluate the proposed three-way cluster bootstrap by independently resampling raters, speakers, and tasks, multiplying row weights by their multiplicities, computing multiplicity-weighted stimulus means, then weighting each stimulus mean by speaker multiplicity times task multiplicity. Do not normalize speaker or task multiplicities away within a stimulus.

Simulate the exact assignment, ties, crossed dependence, stimulus weighting, losses, bootstrap, four fixed contrasts, and Holm correction under the global null and configurations where other contrasts are null or positive. Record null error, interval coverage, power per contrast, and probability all four reject across a declared nuisance grid. The proposed 10,000 draws, recorded seed, two-sided centered-bootstrap p-values, pointwise 95% intervals, and familywise alpha `0.05` remain unfrozen until these simulations validate them.

Before confirmation, the researcher must approve the practically meaningful delta, exclusion rules, assignment, nuisance grid, power target, and final fixed sample design. Use no significance-based optional stopping. If simulation validity fails, revise the method and rerun the pilot gate before collecting confirmatory data.

## Preregistration checklist

- [ ] Natural corpus authorization, consent basis, retention, withdrawal, and speaker split recorded
- [ ] Ethics approval/exemption and rater consent, privacy, recruitment, and compensation process recorded
- [ ] Practical-effect threshold approved by the researcher
- [ ] Five treatment definitions, capability subsets, model, prompts, seeds, extractor, and configs frozen
- [ ] Task set, action-policy envelopes, adjudication rule, and opportunity denominators frozen
- [ ] Output generation, unavailable-output handling, dispatch evidence, and blinding frozen
- [ ] Balanced assignment, randomization seed, exposure limits, and treatment lookup access frozen
- [ ] Rater instructions, response values, rubric flags, and proposed exclusions approved and frozen
- [ ] CSV schema, integrity checks, analysis code, and sensitivity analyses frozen
- [ ] Pilot simulations meet the approved null-error, coverage, and power criteria
- [ ] Confirmatory sample design approved and fixed before its split is opened
- [ ] Claim boundary recorded: utility is perceived matched-output quality; policy compliance is separate; neither validates emotion recognition, real-world benefit, or deployment safety
