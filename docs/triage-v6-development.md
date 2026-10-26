# V6 development observations — not fresh evaluation

These observations precede the frozen V6 model/context matrix. They informed development and receive no held-out accuracy or productivity credit. All files and inference ran from L on the presentation machine. The stronger model was downloading during the early runs and was actually loaded for the final development comparisons below.

## Context and response changes

V6 adds bounded same-class field read/write candidates to the executed-function context. It labels these as static state relationships, not observed object identity or runtime calls. A tightly bounded existing same-module helper may be included as read-only source. New helper calls require exact source evidence and canonical binding checks; protected tests still decide whether a candidate works.

The generator separately verifies the actual native model template and selected non-thinking mode. Its checks reject missing failure citations, unchanged executable replacements, repeated rejected behavior and edits outside complete allowed implementation functions. The original V5 prompt, source and measurements remain unchanged.

## Observed runs

| Development run | Cases | Protected repairs | Observation |
| --- | --- | --- | --- |
| Context inspection, no model calls | Previously exposed V5 e01/e05 | Not measured | Missing constructors became available; e05 also gained a clearly labelled read-only helper. |
| Current model, V6 context | Previously exposed V5 e01/e05 | 1/2 | e01 repaired in 19.834s, including verification. e05 supplied no safe edit in 12.443s. |
| Current model, V6 context | V6 development d01/d02 | 0/2 | Both outputs omitted failure citations; d01 had no edit and d02 repeated unchanged code. Relaxing the citation check would not produce a valid repair. |
| Current model, revised exception context | Same exposed V6 development d01/d02 | 0/2 | d01 produced an unchanged replacement; d02 proposed two changes that failed the unchanged target tests. Neither was applied. |
| Current model, 250-word draft instructions | Same exposed V6 development d01/d02 | 0/2; run excluded from stable-candidate comparison | d01 proposed an exception-preserving change with a uniform indentation mismatch; d02 copied an unchanged, truncated fragment. Only the preparation manifest changed during the run; executable source and prompt bytes remained unchanged. The original drift failure is retained. |
| Current model, manually reduced context, same draft instructions | Exposed d02 only | No repair candidate | The context was reduced to 12,775 characters and the model identified the accepted-count mismatch, but still returned identical 1,200-character fragments. Manual source selection receives no product accuracy credit. |
| Current model, numbered-line draft v1 | Same exposed d01/d02 | 0/2 | Native constrained grammar worked, but both replacements violated the relative-indent contract. Raw output also contains unresolved citation/range/logic errors; shorter output alone did not repair a case. |
| Current model, short instructions plus exact-anchor alignment | Same exposed d01/d02 | 1/2 | d01 was repaired and applied after unchanged target/regression checks; root protected validation passed. d02 again returned unchanged code. Both model inputs and raw outputs exactly match the earlier short-instruction probe. |
| Current model, numbered lines with verbatim indentation | Same exposed d01/d02 | 2/2 frozen checks; **only 1/2 survived semantic audit** | d01 took 17.585s. d02 took 30.095s and two generations, but its denominator is wrong when a classification is abstained. Keep the protected-pass record and the separate counterexample failure. |
| Stronger model, same numbered-line contract | Same exposed d01/d02 | 2/2; d02 counterexample also passed | 18.445s/18.624s, one generation each. Actual diffs preserve the exception object and restore the accepted-classification denominator. This contract is selected for fresh evaluation. |
| Stronger model, short instructions plus alignment draft | Same exposed d01/d02 | 2/2; d02 counterexample also passed | 20.977s/19.748s, one generation each. Retained as an unselected development alternative. |

Times in the second row are workflow observations, not paired comparisons with ordinary debugging. The third row took 10.638s and 12.940s respectively and retained both invalid outputs. No row establishes developer time savings.

The exception-context revision addresses a measured packing loss: the d01 trace already contained 96 functions, but setup paths displaced the error initializer and context manager. Bounded exception-context selection now retains those bodies and both observed sync/async callers within 23,941 characters. Root recomputed this from the original traces and matched the retained draft evidence. These recorded call edges are marked as selection context; they do not prove propagation of a particular exception. The d02 `summarize` body remained unchanged. The subsequent d01/d02 runs took 9.331s and 21.617s; their failures show that better source availability alone did not produce a correct repair with the current model.

The manual e05 diagnostic intervention was also checked: calling its existing canonical helper passes all six protected checks. V5's guard rejected that call; V6's narrow evidence-backed allowance accepts it. This is a manual guard diagnosis, not a model-generated repair result.

The short-instruction probe took 13.832s and 16.015s and applied neither candidate. Root compared every recorded before/after source hash: the only changed file was the draft manifest, which gained its completed preparation checks and additional file hashes. This explains the drift without reclassifying the run as a valid frozen comparison. The d01 raw replacement contains the intended local change but does not match canonical source indentation. In a separate manual diagnosis, root aligned that retained replacement, ran the unchanged source/AST guards and target/regression selections, and independently passed the protected checks. The resulting diff changes only the exception re-raise. This demonstrates recoverable intent, not a live assistant repair or a general permission to reindent strings. The d02 unchanged replacement has no repair to recover. No fresh case or timed trial was used for these observations.

For a separate d02 diagnostic, root manually retained the failing test and its evaluation path while preserving the canonical function text. The 12,775-character input produced a specific explanation of the classification denominator error in 12.010s, but its old/new strings still copied the same initial function fragment up to the schema limit. Source hashes remained stable. This supports investigating output representation as a bottleneck; it does not establish that a general context selector or a changed response schema will repair new failures.

A separate exact-anchor alignment draft subsequently recovered the retained d01 proposal automatically. It requires the entire original old fragment to match one canonical whole-line location after the same uniform space offset; identical but invented surrounding text cannot authorize a match. Only the changed core is applied, with literal contents and all existing validation and test guards preserved. Root ran 188 focused checks and replayed the retained output through the draft, then ran the six target and three regression checks plus independent protected validation. All passed. This remains an exposed-output replay with zero new generation requests; the draft is not an active product change or new accuracy/timing credit.

The first live numbered-line probe used 202/254 output tokens and took 9.102s/8.985s, but produced no applied repair. The next live short-instruction/alignment probe took 13.820s/12.646s: d01 passed all protected checks, while d02 remained a no-op. Root compared the actual generation input hashes and raw response strings against the original short-instruction run: both are identical for both cases. The recovered d01 outcome therefore demonstrates a formatting/validation correction on that known output, without claiming improved model reasoning, fresh-case accuracy, or ordinary-debugging time savings. All implementation hashes remained stable during these later runs.

Raw sanitized reports:

- [Context-only observation](../reports/triage-v6-context-development-01.json)
- [Exposed V5 repair observations](../reports/triage-v6-development-current-context-01.json)
- [Two V6 development cases](../reports/triage-v6-development-current-01-2026-10-26.json)
- [Root exception-context replay](../reports/triage-v6-context-development-02.json)
- [Revised-context development outcomes](../reports/triage-v6-development-current-context-02.json)
- [Short-instruction probe, including source drift](../reports/triage-v6-development-current-prompt-03.json)
- [Manual execution of the retained d01 intent](../reports/triage-v6-d01-intent-manual-01.json)
- [Manual reduced-context d02 diagnostic](../reports/triage-v6-d02-minimal-context-01.json)
- [Automatic alignment replay of retained d01 output](../reports/triage-v6-d01-alignment04-01.json)
- [Numbered-line draft v1 live outcomes](../reports/triage-v6-development-current-lines-01.json)
- [Short instructions and exact-anchor alignment live outcomes](../reports/triage-v6-development-current-alignment-04.json)
- [Current model, numbered-line v2](../reports/triage-v6-development-current-lines-02.json)
- [Stronger model, numbered-line v2](../reports/triage-v6-development-stronger-lines-02.json)
- [Stronger model, alignment draft](../reports/triage-v6-development-stronger-alignment-04.json)
- [Separate semantic audit and counterexample](../reports/triage-v6-development-semantic-audit-01.json)

## Installed-package check

The built wheel was installed outside the source checkout and imported under Python's isolated mode. One-time setup reproduced the actual native model configuration without completion. The installed terminal entry then repaired the already exposed V5 e01 case, applied the tested two-line change and passed the independent protected check. This verifies the packaged integration, not a new accuracy observation or a time-saving comparison. [Retained installed-package run](../reports/triage-v6-installed-wheel-acceptance-02.json) also records an earlier driver argument-construction failure, which stopped before workflow or inference.

After selecting the line2 workflow, a new wheel and source archive were built and the wheel was installed outside the checkout. The installed `debug` dispatch, imported in isolated mode, repaired exposed d01 with the stronger model and passed the independent protected check in 19.808s including CLI startup. The actual diff replaces construction of a new exception with a bare re-raise. [Installed line2 command evidence](../reports/triage-v6-installed-lines-02.json) retains the command, wheel/module hashes and checks. An earlier build without isolation lacked setuptools; that build failure is retained locally, and the normal isolated build succeeded. This is packaging acceptance, not fresh accuracy or saved debugging time.

Exact source hashes and retained local source copies distinguish these observations from subsequent candidates. The V6 matrix, reserved timed trials and historical transfer experiment have separate prespecified protocols. Their results must be reported even when the targets fail.
