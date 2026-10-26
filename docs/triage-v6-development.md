# V6 development observations — not fresh evaluation

These observations precede the frozen V6 model/context matrix. They informed development and receive no held-out accuracy or productivity credit. All files and inference ran from L on the presentation machine; the stronger model was still downloading during these observations.

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

Times in the second row are workflow observations, not paired comparisons with ordinary debugging. The third row took 10.638s and 12.940s respectively and retained both invalid outputs. No row establishes developer time savings.

The exception-context revision addresses a measured packing loss: the d01 trace already contained 96 functions, but setup paths displaced the error initializer and context manager. Bounded exception-context selection now retains those bodies and both observed sync/async callers within 23,941 characters. Root recomputed this from the original traces and matched the retained draft evidence. These recorded call edges are marked as selection context; they do not prove propagation of a particular exception. The d02 `summarize` body remained unchanged. The subsequent d01/d02 runs took 9.331s and 21.617s; their failures show that better source availability alone did not produce a correct repair with the current model.

The manual e05 diagnostic intervention was also checked: calling its existing canonical helper passes all six protected checks. V5's guard rejected that call; V6's narrow evidence-backed allowance accepts it. This is a manual guard diagnosis, not a model-generated repair result.

The short-instruction probe took 13.832s and 16.015s and applied neither candidate. Root compared every recorded before/after source hash: the only changed file was the draft manifest, which gained its completed preparation checks and additional file hashes. This explains the drift without reclassifying the run as a valid frozen comparison. The d01 raw replacement contains the intended local change but does not match canonical source indentation; its correctness still requires independent protected execution. The d02 unchanged replacement has no repair to recover. No fresh case or timed trial was used for these observations.

Raw sanitized reports:

- [Context-only observation](../reports/triage-v6-context-development-01.json)
- [Exposed V5 repair observations](../reports/triage-v6-development-current-context-01.json)
- [Two V6 development cases](../reports/triage-v6-development-current-01-2026-10-26.json)
- [Root exception-context replay](../reports/triage-v6-context-development-02.json)
- [Revised-context development outcomes](../reports/triage-v6-development-current-context-02.json)
- [Short-instruction probe, including source drift](../reports/triage-v6-development-current-prompt-03.json)

## Installed-package check

The built wheel was installed outside the source checkout and imported under Python's isolated mode. One-time setup reproduced the actual native model configuration without completion. The installed terminal entry then repaired the already exposed V5 e01 case, applied the tested two-line change and passed the independent protected check. This verifies the packaged integration, not a new accuracy observation or a time-saving comparison. [Retained installed-package run](../reports/triage-v6-installed-wheel-acceptance-02.json) also records an earlier driver argument-construction failure, which stopped before workflow or inference.

Exact source hashes and retained local source copies distinguish these observations from subsequent candidates. The V6 matrix, reserved timed trials and historical transfer experiment have separate prespecified protocols. Their results must be reported even when the targets fail.
