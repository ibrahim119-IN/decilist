# V6 results: improvement gate not met

The frozen V6 candidate at commit `8442a08` did not establish an overall debugging-time benefit. The stronger local model with V6 context repaired 1/6 evaluation defects, below the prespecified 4/6 gate. It repaired one of four reserved tasks automatically; the other assisted sessions required manual continuation. No human study ran.

## Fixed model/context comparison

All 32 planned observations completed against the same frozen code, output contract and tests. Each cell covers the same six evaluation defects plus two controls, rather than six new independent defects per cell.

| Model | Context | Protected evaluation repairs |
| --- | --- | --- |
| Qwen3-Coder-30B-A3B Q4_K_M | V5 | 0/6 |
| Qwen3-Coder-30B-A3B Q4_K_M | V6 | 1/6 |
| Qwen3.6-27B Q8_0, non-thinking | V5 | 1/6 |
| Qwen3.6-27B Q8_0, non-thinking | V6 | 1/6 |

The successful change in each applicable cell reversed index deletion order when removing repeated headers; root inspection confirmed the actual diff. The already-passing control made zero generation requests in all cells. The mixed-failure control was not repaired in any cell. All failures, no-ops, invalid proposals and unsuccessful revisions remain in the reports.

## Reserved end-to-end time

Fresh GPT-6.1-sol/xhigh agents used normal tools or one initial local-assistant workflow, then continued manually when necessary. Timing includes unsuccessful assistance, inspection, editing, tool waits and protected verification. The root reran unchanged protected checks and inspected every completed diff.

| Case | Normal tools, seconds | Assisted, seconds | Assisted minus normal | Initial assistance |
| --- | ---: | ---: | ---: | --- |
| r01 | 33.344 | 54.172 | +20.828 | No safe intervention; manual repair |
| r02 | 32.703 | 52.157 | +19.454 | Rejected new-call proposal; manual repair |
| r03 | 39.875* | 130.953 | +91.078* | Failed candidate then invalid revision; manual repair |
| r04 | 43.688 | 22.891 | -20.797 | Applied verified repair |

The original slot-03 ledger was found to contain 96,203 NUL bytes after an interruption; its clock cannot be recovered. The record is retained unchanged. `*` identifies a separate fresh-agent operational replay, not the original observation. Consequently the complete first-observation time gate is unavailable and is not passed. The first-observation report preserves the missing slot and invalid record separately.

Using that explicitly labeled replay only for a descriptive comparison, all eight final repairs passed their six unchanged protected checks, but only 1/4 pairs was faster with assistance. The normal median was 36.610 seconds versus 53.165 seconds assisted, approximately 45.2% slower. This does not meet the 3/4-faster and 20%-lower-median targets. The sole automatic repair correctly aggregates every provider attempt instead of only each decision's final attempt.

These are four paired seeded defects and agent sessions. They do not establish human productivity, representative accuracy or general minutes saved. Setup/model loading is separate from warm session timing. The operational replay and resumed sessions used the same verified weights, runtime settings and closed-thinking profile after a server restart; fresh readiness was checked before assistance.

## Next implementation, based on observed failures

Selection missed a second executed exception handler sharing failed-test coverage and omitted observed implementation callers of callbacks defined inside tests. These are concrete context-packing defects. Other cases already exposed the relevant code but received incorrect or unchanged proposals, so adding context alone cannot explain every failure.

The old independent start/end-line grammar allowed responses exceeding the 16-line edit limit; validation correctly refused them. The next candidate will constrain the model's line count directly, preserve exact-source and syntax checks, and retain safe abstention. Rejected proposals adding unrelated calls do not justify broadening the patch allowlist. New source-selection and wire-contract changes require targeted tests, exposed-case development replay, a new candidate freeze and untouched historical transfer tasks before any new speed claim.

Evidence: [matrix summary](../reports/triage-v6-matrix-first-01-summary.json), [original timing report](../reports/triage-v6-agent-trials-first-01.json), [timing with labeled operational replay](../reports/triage-v6-agent-trials-recovered-01.json). Full private records, the damaged ledger, model launch evidence and the 60-file frozen candidate snapshot remain on L.
