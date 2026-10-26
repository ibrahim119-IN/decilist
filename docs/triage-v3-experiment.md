# V3: separating model capacity from assistant design

Protocol drafted on October 26, 2026, before new-case model measurements. This is an engineering experiment, not a human productivity study. Previous results remain intact.

## Why the previous comparison was inconclusive

The previous four repair cases were deliberately small, one-line defects. Normal-tool participants were fresh GPT-6.1-sol/xhigh agents, not human developers. Their median completion time was 27.04 seconds. Four assisted runs added 2.001 seconds of hint requests in total, plus tool/reading overhead; their median was 31.16 seconds. Two hints came from rules without calling Liquid, and two model hints abstained. Those timings cannot isolate a model-speed effect, nor establish human time savings.

The assistant returns one of six broad inspection areas. Even a correct classification can add little beyond the traceback already visible to a capable developer. Bounded source enrichment also missed the expected implementation path in three of eight new evaluation cases. A model cannot reliably diagnose code it has not seen. Conversely, 64 development-only case/prompt measurements did not resolve the low coverage, so wording alone is not an adequate explanation.

## Local model candidate

The larger coding candidate is [Qwen3-Coder-30B-A3B-Instruct](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct), using the [ggml-org Q4_K_M conversion](https://huggingface.co/ggml-org/Qwen3-Coder-30B-A3B-Instruct-GGUF) at revision `0ca51122012fe4a20dc82988ee58780b008ce4e3`. Its coding-oriented training motivates the experiment; parameter count and publisher benchmarks do not establish superiority on this workload. Quantization, runtime, prompt format and probability extraction differ from Liquid and must be recorded as confounders.

The existing Liquid runtime stays unchanged. The new model uses an isolated cache and loopback service on the other RTX 3090. Exact artifact hashes, actual memory use, load time, failures, and compatibility are recorded after execution. Installation/download time is separate from warm decision time. Assistant-model comparisons use local inference only, without an external model API. The separate repair participants are Codex GPT agents; those participant runs are not local-model inference or human trials.

## Prespecified comparisons

| Model | Generic route design | Grounded action design |
| --- | --- | --- |
| Liquid d1-3B | Existing six inspection areas | Select one concrete inspection derived from observed source/facts |
| Qwen coding candidate | The same six inspection areas and evidence | The identical concrete action list and evidence used for Liquid |

Both designs receive the same compact evidence for each case. Grounded actions are generated deterministically from that evidence, never from catalog labels or reference repairs. The complete candidate list is retained, including whether an expected target was available at all. Manual review remains an explicit choice. No authored answer-containing multiple-choice sets enter model inputs.

Private option keys stay inside both adapters. Qwen sees only option aliases and their meanings, matching the semantic choice information exposed by Liquid's native alias interface. The surrounding native/chat prompt formats still differ and remain a model/adapter confounder.

A no-model baseline selects the first non-review grounded action from that same list. It is scored with the same source-target rubric and has no learned confidence. This separates value supplied by deterministic evidence/action construction from value added by model ranking. Candidate order is retained; no post-result reordering is allowed.

Raw top-choice accuracy is reported separately from acceptance at the unchanged illustrative 0.7 policy. Model confidence is not calibrated, and a 0.7 token-conditioned probability is not equivalent to a 0.7 native Liquid decision estimate. Missing option probabilities, malformed responses, unavailable models and context failures are errors, not confidence-one answers. Probability extraction must pass actual protocol smoke checks before the assistant freeze.

Generic-route accuracy and grounded-action target selection are different metrics and will not be combined into one misleading score. A source-target match shows an inspection candidate, not proven causal diagnosis. Comparisons between models within one design are more interpretable than percentages across different tasks.

A separately labelled Qwen-only diagnostic probe may generate a bounded hypothesis, existing source reference, falsifying check, and expected observation. It returns no invented confidence. Validation requires grounded references and structured fields. This is an additional design capability, not a complete two-model factorial cell: Liquid's native decision interface does not generate such explanations. Any causal-correctness judgment is explicitly distinguished from syntax/grounding checks.

Development-only diagnostic revisions are retained: the initial status-first schema returned review on h01/h05; moving status last produced contradictory nonempty hypotheses plus review; an explicit positive contract and removal of duplicated source text still produced those contradictions. The selected representation has one nullable hypothesis and grounded references, with report status derived after validation. A null hypothesis still represents review; contradictory old outputs are not reinterpreted as accepted. The diagnostic prompt now sends extracted facts and one source catalog, retaining implementation provenance, rather than repeating source excerpts in both compact text and the catalog. This differs from the unchanged compact evidence used by both models in the four ranking cells. These are development choices on two known cases, not held-out improvements. Valid hypotheses or source hits do not establish causal correctness; an observed merge-versus-append misconception remains a development limitation.

Before seeing Qwen output, the manual hypothesis rubric is declared as follows: `specific_and_supported` requires the hypothesis and proposed observation to explain the distinguishing mechanism in the frozen root-cause facts, supported by the supplied evidence and without contradicting visible code; `partial` identifies relevant facts or an area but omits the distinguishing mechanism; `unsupported_or_wrong` contradicts the evidence/seed or relies on an unavailable fact as if it were observed; `no_hypothesis` covers review and generation errors. Source-target matching remains a separate field. The primary engineer performs this assessment with access to the frozen rubric; it is not an independent blinded judgment. Each judgment retains the generated text and a concrete reason, and never replaces protected repair verification.

## New cases and repair measurements

The corpus is designed before observing model outputs: eight new seeded regressions in pinned Decilist/HTTPX source (two development, six evaluation), two mixed/insufficient controls, and two reserved repair cases. It uses cross-function, sequencing and shared-state invariants rather than only direct bounds/duplicate-index mistakes. Structural difficulty indicators are recorded; actual greater difficulty is not assumed. Every case must pass baseline → fail mutation → pass reference repair with protected tests.

Development cases can guide implementation and prompts. The assistant source, model/runtime identities, evidence algorithm, action generator, policy, output schema and evaluator are frozen before evaluation cases are called. Evaluation labels and reference repairs never enter the input. If a post-freeze defect requires a change, preserve the first attempt, disclose the change and treat subsequent reuse as a replay.

Development inspection before any new-model call exposed missing lexical receivers inside `async with` and unresolved HTTPX call chains through static star reexports. V3 evidence changes address those generic cases in separate modules; V2 sources and results remain intact. Earlier development evidence is retained to show what changed. During implementation, the coordinator and action author encountered embedded non-development test contracts while locating lab APIs. They did not receive model outcomes or use evaluation/trial oracle repairs for tuning. This is not a fully blinded implementation team; reserved repair participants are separate fresh agents.

The two reserved cases are excluded from all machine tuning/evaluation. Four treatments are declared in advance: normal tools, Qwen generic route, Qwen grounded action, and Qwen bounded diagnostic. Each case/treatment has a new GPT-6.1-sol/xhigh agent with no conversation history or reference answers. Each hint is called live inside the timer. The endpoint is a protected passing repair, not the participant saying it is done. Failures, tool errors, elapsed time, test runs and abstentions remain in the record. These are eight runs on two independent cases, not eight independent defects or human participants.

All three hint treatments use the same concise product text formatter; full JSON reports remain saved for audit. Each assisted participant makes one initial live hint attempt inside the clock, before inspecting case source or the original log; errors and abstentions remain part of the assigned treatment. No substitute treatment or additional hint is requested. The original failure log and permitted source files remain available in every arm after that attempt. Participants receive an editable-file allowlist, which narrows the search compared with an unrestricted real repository. Presentation is fixed before the first trial.

## Interpretation rules

- Better Qwen choices within the same design would support a model/adapter capability contribution; it would not isolate parameter count, quantization or training as the cause.
- Missing correct targets in the shared evidence/action candidates points to an evidence/design limitation that changing the model cannot resolve.
- Useful bounded diagnostic output with weak route-only usefulness would support changing the assistant's output design. Grounded prose alone does not prove a correct hypothesis or faster repair.
- Agent time differences remain descriptive on a tiny sample. No percentage of human time saved will be claimed.
- Negative results remain published. No threshold reduction, relabeling, selective removal or new-case claim after tuning on evaluation output.
