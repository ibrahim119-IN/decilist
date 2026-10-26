# V5: complete evidence, tested repairs, and time to repair

This protocol precedes all V5 local-model quality calls. V4 achieved zero protected repairs in six new evaluation defects; all four paired assisted agent trials took longer. V5 addresses measured context omissions and overbroad response rejection. A context hit, plausible explanation, or accepted JSON is not a repair. The objective is an earlier protected passing repair, including unsuccessful assistance and all workflow overhead.

## Scope and retained evidence

V2/V3/V4 sources, protocols, negative results and freezes remain unchanged. New V5 modules retain late runtime evidence, pack connected complete function bodies within the actual serialized budget, and distinguish harmless quoted identifiers from commands or unsupported execution claims. An evidence-backed hypothesis without a safe exact intervention remains `needs_review`, never a repair. No fabricated edits are required for schema compliance.

The trusted-project CLI reproduces tests in a bounded disposable copy, gathers runtime evidence, proposes at most two exact production-source replacements, and tests the intervention. It allows at most two generations, with the second allowed only after an actual first candidate test rejection. Parser/guard failures are retained, not retried until a favorable output appears. Passing target and separate regression tests, unchanged collection/protected files, and source integrity are all required. Explicit `--apply` transfers only a verified candidate after source rechecking. Existing tests and configuration cannot be edited by the assistant. A fresh directory is required; missing safe ancestors may be created. Original source is unchanged by default.

Execution uses the caller's Windows permissions on explicitly trusted projects. Copies, environment filtering and AST checks do not constitute an OS security sandbox. Tracing excludes frame values, arguments, return values and environment contents. Dynamic failure text is opt-in. Retained raw test output can contain sensitive values; publication requires review/redaction. All new work, caches, snapshots and temporary files stay under `L:\CodexData\Decilist`.

## Fixed model and development boundary

Use the already running Qwen3-Coder-30B-A3B-Instruct Q4_K_M through llama.cpp b11889, one inference slot and context size 16,384, with the default workflow context budget of 24,000 characters. Freeze actual model/configuration/server/build identities and all generation parameters before evaluation. Loading and downloads are reported separately from warm task latency. Any different model or runtime is a new treatment.

Development may use clearly labeled replays of already exposed V4 cases and V5 development cases d01/d02. The corpus author does not inspect/tune against V5 assistant implementation or model outputs. V5 evaluation and reserved case details stay outside assistant-author prompts until candidate freeze. This separation is procedural, not a claim of independent blinded research.

V5 uses pinned real Decilist and HTTPX source, retaining the original Apache-2.0 and BSD-3-Clause licenses respectively. It has two development cases, six new evaluation cases, four new reserved timing cases, and two controls. Defect mechanisms must differ from the earlier corpora. All regression fixtures require observed original pass → seeded failure → reference-repair pass, with unchanged protected tests and separate regression invariants. Ineffective seeds and authoring corrections remain recorded. c01 is an already-passing project, which must stop before inference. c02 is a real mixed/multiple-cause failure: all unchanged tests still have to pass. It is not an initial-log withholding control. The active workflow may rerun trusted tests even when the initial log is incomplete.

Freeze all imported product, worker, evidence, verification, measurement, trial, lab, catalog and protocol sources before opening held-out cases. Verify source and actual runtime identity first. Run e01–e06 and c01/c02 in fixed order, once each, retaining every outcome. A subsequent correction on those cases is a development replay, never fresh accuracy. Do not delete an unfavorable run, select a fastest retry, relax correctness, or repeatedly sample until a win.

## Engineering acceptance checks

The prespecified engineering target is at least four protected passing repairs among the six fresh evaluation defects, no source/test-integrity violations, and a passing c01 baseline with zero inference. c02 is reported separately with its exact outcome and costs; abstention is not repair success. This small gate is a development decision, not an estimate of general model accuracy.

Run all eight reserved trials even if the quality gate fails, to measure the complete workflow's current cost and locate remaining bottlenecks. For the small agent experiment, the time target requires no reduction in protected repair success versus normal tools, at least three of four paired defects faster with assistance, and at least a 20% reduction in the descriptive median elapsed time across the four assisted slots versus the four normal slots. These thresholds are fixed before data and do not establish statistical significance or human time saved. Report a failed gate plainly and continue with fixes justified by retained evidence; confirmation of a revised candidate requires a new separately authored case set.

Report attempted, generated, structurally valid, patched, target passed, regressions passed, protected repair passed, guard/error and `needs_review` counts separately. Retain exact model outputs locally, explicit diagnostic reasons and the actual serialized context and its hash. Public redacted context must have its own hash and identify that it differs from private evidence. Report snapshot, baseline, context, model, verification, application and total wall time, including failed stages. The enclosing measurement clock includes report persistence and failed assistance.

## Eight reserved agent runs

Each slot uses a fresh GPT-6.1-sol/xhigh agent without parent history, no oracle, no previous attempts and no narrow faulty-file hint. Run sequentially to avoid a local-model queue. Both arms get equivalent neutral task information, broad editable production roots, existing protected tests and a 900-second cap. Fixed balanced order:

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

The clock starts before failure/source/assistant inspection. Assisted participants make exactly one initial live workflow call inside the clock and may apply its verified candidate. Internal bounded feedback counts as part of that call. On failure or abstention they continue manually in the assigned arm; no substitute hint, extra model or cost subtraction is allowed. Normal participants use ordinary development tools and the same trusted test commands. Both arms record actions with the same minimal overhead. Completion is an observed protected passing repair, not an agent assertion. Every timeout, formatting/integration failure and invalid record remains visible.

Root reruns protected validation outside the participant clock and inspects real source changes, unchanged tests, allowed scope, source/collection hashes, recorded phases and assistant attempts. Local mutable records and instruction-based isolation cannot establish the absence of unrecorded reads. Report every paired elapsed difference, success/failure and the descriptive medians. Eight runs are four paired defects, not eight independent human tasks. New multistep tests do not prove that real developers perceive them as harder.

## Human benefit and delivery

Agent experiments are engineering evidence. They cannot be labeled human productivity results. The previously prepared human study remains unrun and untouched. A concrete simple CLI/SDK flow, installation instructions and a replay of retained results must accompany the implementation. A future human study requires actual consenting participants, unfamiliar counterbalanced tasks, start-to-protected-repair timing and retention of failed cases and exposure information. No recruiting messages are authorized here. No human savings or completed general-purpose debugging product is claimed without the corresponding observed evidence.
