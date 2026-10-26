# V6: object-state context, a stronger local model, and measured repair time

This protocol is written before any V6 quality call or V6 held-out case inspection by assistant authors. V5 repaired 2/6 new evaluation defects, below its 4/6 target, and its four assisted agent trials had a 67.875-second median versus 58.703 seconds for ordinary tools. V6 must improve actual protected repairs and complete task time. More source, accepted JSON, a correct file, or a plausible explanation does not receive repair credit.

## Changes and development boundary

Preserve V2–V5 sources, freezes, raw attempts, negative outcomes and clocks. New versioned V6 modules may add bounded object-state producer evidence: an executed read can be linked to observed assignments in the same lexical class and observed calls supplying those assignments. State relations are labeled separately from observed call edges. Any unobserved static helper must be explicitly marked unobserved and read-only; it cannot acquire invented runtime coverage or edit permission. No benchmark case names, source-specific repair rules, local variable values, or repair oracle may guide collection.

The response parser retains exact-source, syntax, no-op, repeated-intervention, command, unsupported-claim and patch screening. A specific hypothesis without a safe candidate is `needs_review`. It is not repaired or malformed merely for abstaining. Do not relax screening to accept a known wrong V5 patch. If a generic false rejection is demonstrated during development, retain that example, the earlier behavior and tests before a versioned correction.

Development inspection found one such distinct false rejection: an exact intervention calling an existing unchanged local helper was rejected by the old callable-shape rule, although all six unchanged protected checks passed. V6 may admit that class of call only when the complete canonical helper is supplied as explicitly unobserved read-only context and cited, its unique same-module binding and unchanged source are verified, and a bounded static call screen rejects unknown/dynamic/dangerous paths. No helper edits, added imports, fabricated execution edges or general new-call allowance follow from this exception. The same V6 rule applies in all four comparison conditions; context that omits the helper cannot authorize its use. Iteration and otherwise permitted calls can still execute trusted project behavior, so this remains screening rather than an OS security boundary.

Already exposed V4/V5 cases and V6 d01/d02 are development only. Corpus authoring is separate from assistant development: the corpus author must not inspect V6 implementation or model outputs, and assistant authors must not inspect V6 evaluation/reserved mechanisms or reference fixes before candidate freeze. This procedural separation is not a claim of an independently blinded study. Preserve every development attempt with exact source and model identity; never relabel an exposed case as fresh.

## Models and controlled comparison

Two local treatments are specified before new data:

1. Existing Qwen3-Coder-30B-A3B-Instruct Q4_K_M, artifact SHA-256 `f6050b4da22869aa3fb9d94bb3314e9fc5a027d4741c1f97761c387d6b2b2335`.
2. Qwen3.6-27B Q8_0 from ggml-org revision `1132d03976d88e30a09ab0ddbf1531d5e1b46e7c`, artifact SHA-256 `73f8260284708ed78ae266df672288b6ad1f2c73ec7ffeb7514b5cecdba646c9`, 28,595,762,464 bytes. This is the preselected treatment for reserved timing trials, irrespective of which model wins evaluation.

The intended runtime is pinned llama.cpp b11889 (`d854d98bae598388ecea1ed84396587e5edc326d`), 16,384-token context, one slot, temperature zero, seed 42, no prompt cache, maximum 1,200 generated JSON tokens and existing 1,536 provider hard cap. Retain the exact executable hash, weights hash, aliases, native readiness fields, template identity, launch settings, generation parameters and actual memory/device allocation before evaluation. Downloads, initial loading and model switches are separate from warm task times and must be reported.

The stronger model uses an explicitly verified **non-thinking** profile for the bounded latency experiment. For this runtime, `--reasoning off` supplies `enable_thinking=false`; the actual no-override `/apply-template` result must prove the expected closed-thinking prefix. A label or response stripping alone does not establish mode. Do not run quality calls if the actual template/mode disagrees with the frozen profile. This short JSON experiment does not test unrestricted reasoning-mode capability. If fit or compatibility fails, record it; any replacement model/runtime/profile needs a dated protocol amendment before held-out cases are opened, not a silent substitution.

Use a 2 × 2 engineering comparison on the same six new evaluation cases: model (existing/stronger) × context packer (retained V5/new V6). All four use the same V6 workflow, generator instructions, output schema, safety checks, 24,000-character serialized context budget and test commands. Only the named context policy and model profile differ. This isolates the context change from the model change more directly than comparing unrelated corpora. A model-profile difference includes quantization and deployment differences, not model architecture alone.

After both identities and all sources are frozen, run fixed batches: existing model/V5 context, existing model/V6 context, stronger model/V5 context, stronger model/V6 context. Within each batch run e01–e06, then c01/c02, once in fixed order in separate fresh copies. Run all batches even after a poor result. No tuning, extra samples, result-conditioned ordering or faster-retry selection between batches. Model slots run sequentially with no concurrent quality requests. Repeated observations of the same six defects are paired engineering observations, not 24 independent tasks. If an operational interruption invalidates a run, retain it and distinguish any repeat from the prespecified first observation.

## Cases and success

Use pinned real Decilist source at `8327d24aceee015af3c79bf8a679cd7aad6917f7` and HTTPX 0.28.1, retaining Apache-2.0 and BSD-3-Clause licenses. V6 contains two development, six evaluation, four reserved timing cases and two controls, with mechanisms different from V2–V5. Each seeded fixture needs observed original pass → seed fail → reference repair pass and separate unchanged regression checks. c01 already passes and must stop before inference. c02 has a real mixed failure; report exact behavior and all costs, without special-casing initial logs. Retain ineffective seeds and authoring corrections.

Freeze corpus source/catalog/base identity independently of the actual retained fixture proof. Portable source checks must never claim an absent historical proof was verified. The evaluator requires actual exact retained proof and successful fresh fixture execution, not a declaration in a manifest. Freeze all imported product, worker, packer, provider, measurement, trial, lab and protocol sources before inspecting held-out case details.

The quality gate for the preselected stronger/V6 treatment remains at least **4/6 protected repairs**, no source/test-integrity violations, and zero inference on passing c01. Report both controls separately. A protected repair requires successful target and separate regression tests, unchanged protected files and test collection, the permitted production scope, and source integrity; source citations or passing an incomplete subset do not count. Root independently executes these checks and inspects actual changed source. Tests support an intervention; they do not prove exhaustive correctness.

The workflow takes a disposable copy of an explicitly trusted project, reproduces actual failure, builds context, proposes at most two exact replacements, and tests them. It allows at most two generations, the second only after the first candidate really failed unchanged tests. Parsing or patch-screening failure does not trigger sampling until success. Applying to the original requires explicit `--apply` and source rechecking. Run under caller OS permissions; copies and AST checks are not a security sandbox. Keep new work/models/cache/TEMP on L. Tracing must not capture frame values, arguments, returns, credentials or environment contents. Review raw logs before publication and retain separate hashes for exact private and redacted public evidence.

## Reserved time-to-repair experiment

Run all eight reserved trials with the preselected stronger model/V6 context even if the quality gate fails, unless runtime compatibility or integrity makes execution impossible. Report such a blocker instead of substituting a model. Fresh GPT-6.1-sol/xhigh agents, no parent history or prior trial, get equivalent neutral task descriptions, broad production roots, unchanged protected tests, ordinary tools and a 900-second cap. No narrow faulty-file hint or access to fixture catalogs/oracles/previous trials. Sequential balanced order:

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

The clock starts before source, failure or assistance inspection. Generic trial API imports may happen before the clock, but task information may not. The assisted arm makes exactly one initial live workflow call inside the clock, with bounded internal feedback included. A verified `--apply` candidate may complete the task through the protected trial verification. On failure or abstention, continue manually in the same arm; no alternate model, substitute hint or subtraction of failed assistance time. Both arms record the same minimal ledger and finish only after observed protected success. Preserve timeouts, formatting failures, invalid records and unfinished repairs.

Root reruns protected checks after the clock, verifies actual source/protected/collection hashes and allowed diffs, and reports every slot and same-case signed difference. The time gate remains equal protected success to ordinary tools, at least **3/4 pairs faster**, and at least **20% lower descriptive median elapsed time** for assisted slots. Include the entire workflow, waiting, failed attempts, manual continuation and logging. Report stage times and exact assistant outcomes to explain overhead. Local mutable ledgers cannot prove absence of unrecorded reads. Four paired defects and eight agent sessions are not eight independent human tasks.

## Decision and delivery

Publish all prespecified first outcomes, every development iteration, identity, acceptance-gate decisions and remaining failure mechanisms. A failed gate triggers concrete further implementation and a new fresh-case comparison, not a claim that locating code is sufficient. Shipping evidence must include a simple terminal/Python integration in an existing project without a Playground/form, and a reproducible replay of actual results.

No human study has run. Agent timing cannot establish human productivity, perceived difficulty, representative accuracy or general minutes saved. A later human claim requires actual consenting developers, unfamiliar counterbalanced tasks, start-to-protected-repair timing and retention of failures/exposure. Do not fabricate participant actions or recruiting messages.
