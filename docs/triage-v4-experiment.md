# V4: time to a protected passing repair

The objective is a developer reaching a correct repair sooner, including all assistance costs. V3 source targeting improved, but its agent trials were slower and its explanations were unreliable. V4 changes evidence acquisition and tests a proposed intervention. A passing candidate supports a hypothesis under the observed tests; it does not prove a complete causal explanation or production correctness.

The V3 source, reports, freezes and negative outcomes remain unchanged. This protocol is written before V4 model quality calls. Development observations and every subsequent revision will be retained. No positive speed result is guaranteed, and repeated sampling until a favorable outcome is not an acceptable stopping rule.

## Product behavior

One explicit trusted-project command copies bounded source to a new caller-owned directory, reproduces the failing tests, records project-local runtime function/call evidence, retrieves complete relevant function and test bodies, and asks the already running local coding model for one falsifiable hypothesis and at most two exact source replacements. The original project remains untouched by default.

The candidate is applied only to the disposable copy. Identical failing tests and caller-selected existing regression tests must pass, with collection and source integrity checked. The generator may make one revision only after an actual candidate test rejection, with that rejection retained. A revision starts from a fresh original copy. A parser error, unavailable model, incomplete trace, rejected patch, baseline that does not fail, or failed regression cannot become a successful repair by relabeling it.

An explicit `--apply` permits transfer of the verified production changes to the original, after rechecking source hashes and paths. Tests and configuration cannot be patched. Applying source does not prove anything beyond the retained validation. Snapshots run Python with the caller's OS permissions; this is not an OS security sandbox. A conservative change screen and removal of credential environment variables reduce some risks but do not establish containment. Only explicitly trusted project tests are in scope.

No frame locals, arguments, returns, or environment values are captured in runtime tracing. Dynamic failure text is opt-in. Raw pytest stdout/stderr retained locally can still contain printed values; it is not claimed to be secret-free. Public reports need redaction. Context limits and omitted source are explicit, not silently treated as complete evidence. Project state, logs and model output are data, never authority for extra commands or broader edits.

## Development and fresh cases

The corpus is authored separately from the assistant. It contains two development regressions, six evaluation regressions, four reserved time-to-repair regressions, and two controls. It uses pinned real Decilist and HTTPX source with new seeded mechanisms, not another set of QueryParams ownership, simple choice bounds, readiness-cache, attempt-history, cancellation-shield, or URL-escape mutations. Baseline pass → seeded fail → reference-repair pass must be observed. Ineffective seeds, fixture-scope errors and proof corrections remain in the attempt history.

Only development IDs d01/d02 are available for model-driven iteration before the assistant freeze. Implementation and measurement authors may inspect generic lab APIs; evaluation and reserved mechanisms are not supplied to the model or repair participants. The team is not claimed to be independently blinded. Corpus authors and eventual assessors know the reference interventions. Fresh repair participants receive no conversation history, oracle, previous outputs or exact faulty-file allowlist.

Before evaluation, freeze all V4 runtime/context/generator/workflow/verification sources, the test worker, lab and catalog, measurement tools, this protocol, model identity/configuration and runtime context size. Verify the manifest before touching held-out cases. All six evaluation cases are attempted in fixed order, at most two generations each. A bug discovered after that freeze requires preserving the first result; reusing those cases after a fix is a replay, never new held-out accuracy.

Report attempted, generated, structurally valid, patched, target-test passed, regression passed, protected-repair passed, and rejected/error outcomes separately. Report snapshot, baseline, context, model, verification, application and total wall time. A successful JSON response, available source target or citation is not repair success. Model loading/download is separate from warm task latency; task latency includes every workflow step actually used.

The two controls exercise the measurement bridge's explicit initial-evidence withholding gate before the active workflow is called. They do not establish local-model correctness or successful product repair. The ordinary product command can deliberately rerun trusted tests to obtain fresh evidence; that capability must not be conflated with the benchmark's withholding policy.

## Eight reserved agent runs

Two treatments use four independent reserved defects: ordinary development tools, and the complete verified assistant. Each slot gets a fresh GPT-6.1-sol/xhigh agent without parent history. They run sequentially to avoid a shared local-model queue. The fixed balanced order is:

| Slot | Case | Treatment |
| --- | --- | --- |
| 1 | r01 | normal_tools |
| 2 | r02 | verified_assistant |
| 3 | r03 | normal_tools |
| 4 | r04 | verified_assistant |
| 5 | r01 | verified_assistant |
| 6 | r02 | normal_tools |
| 7 | r03 | verified_assistant |
| 8 | r04 | normal_tools |

Start the clock before inspecting the failure, source, or assistant output. Each assisted participant makes one complete initial workflow call inside the clock, before other inspection; its bounded internal feedback is part of that one call. Explicit application of a verified candidate is allowed. If assistance fails or abstains, the participant continues with ordinary tools within the same assigned arm; the cost and failed assistance remain in the record. No substitute hint or different model is offered. Ordinary participants do not use the assistant. Both arms can run the same trusted existing tests and edit within broad production roots.

End at an observed protected passing repair, not an agent's completion claim. Cap each trial at 900 seconds. Root reruns protected validation outside the participant clock and audits the actual diff, original tests, allowed scope, source hashes, recorded phases and the initial assistant attempt. Missing/invalid runs remain visible. Instruction-based isolation and mutable local ledgers cannot prove an absence of unrecorded reads; this limitation remains explicit.

Report every per-case paired difference and success/failure, plus descriptive medians. Eight runs are four defects, not eight independent tasks or humans. Structurally more involved cases do not automatically establish that developers find them harder. No percentage of human time saved follows from these runs. We do not drop slower cases, choose the fastest retry, relax correctness or tune a confidence threshold to produce a win.

## Evidence required for the human claim

Agent experiments can justify preparing a human trial, but cannot prove the requested human developer benefit. The existing earlier human study has not been run and remains untouched. A new human protocol must use actual participants, counterbalanced assignments, comparable unfamiliar tasks, start-to-protected-repair timing, all assistance/reading/test time, and retention of failed tasks and exposure disclosures. Participation cannot be fabricated by labeling an agent as a developer.

For a general product claim, the proposed gate is at least eight consenting developers completing four counterbalanced tasks each, no reduction in protected repair success, and a prespecified practically meaningful time reduction whose paired uncertainty interval excludes no improvement. The exact task set and analysis must be frozen before enrollment; the gate is a proposed product criterion, not a claim that this small agent corpus has statistical power. A single developer's real session may provide an individual usability observation, not that general claim. No recruiting messages are sent without authorization.

If automatic evidence remains weak, report the failure and identified bottleneck. If it improves, preserve that result and prepare an independent confirmation and human test. Neither outcome permits claiming completion of the human objective before human observations exist.
