# Bounded source reservations and line-count responses

The next candidate corrects two observed context-selection defects and changes the model's edit representation. It keeps the public `decilist debug` command and canonical verification pipeline. The earlier frozen V6 evidence is retained in [V6 results](triage-v6-results.md).

The selector reserves up to two observed implementation callers of callbacks contained in failing-test source. Exception packing preserves failed-test diversity but can retain two distinct executed handlers from the same test. It packs both anchors and their observed small helpers before outer callers exhaust the reservation. The existing overall source/depth/privacy limits and 24k canonical/model budgets remain. This is selection evidence, not inferred object identity or proof of causality.

Wire version `evidence_absolute_lines_verbatim.3` replaces independent start/end lines with an absolute start and strict count of 1–16 lines. Canonical bounds, overlap/literal handling, verbatim indentation, citations, no-op/repeated-edit detection, AST/helper screening, actual tests and explicit apply remain. No patch allowlist or generation budget was broadened. The instructions also ask the model to trace the failed value to its computation and reconsider an already-present proposed change.

Trial writes now flush/fsync their own unique temporary file before replacement and preserve the old ledger on failed writes. This reduces an interruption risk; it does not establish the cause of the earlier damaged record or guarantee survival of every storage failure.

## Observed development results

One fixed ten-case run used the same stronger local profile on already-exposed cases, with unchanged source throughout. All model attempts and failures are retained. These are tuning observations, not new accuracy or productivity evidence. Non-timed unit testing could overlap this run, so its durations are diagnostic only.

| Exposed group | Protected repairs | Outcome |
| --- | --- | --- |
| d01, d02 | 2/2 | Exception re-raise and accepted-classification denominator corrected |
| e01–e06 | 2/6 | e01 exception mapping and e04 header deletion corrected |
| r01, r03 | 0/2 | Failed/rejected proposals and abstention retained |

The original stronger/V6 evaluation repaired 1/6 of the now-exposed e01–e06 cases. After the combined selector/contract/instruction changes, e01 also passed. This joint change does not isolate a causal effect of each component. e02/e05 still returned no-ops; e03 failed patch screening; e06's candidate failed tests and its revision repeated the intervention. Merely including the missing source did not guarantee a correct edit.

Root inspected all four successful diffs. The earlier mixed accepted/abstained/score counterexample also passed the new d02 edit in an isolated process, returning accepted accuracy 1.0. The code was built as a wheel and source archive, installed outside the checkout, and its actual isolated `debug` dispatch repaired exposed d01 and passed protected verification in 19.603 seconds including CLI startup. Installed CLI, workflow, context and generator modules matched the current source bytes.

Root full-suite result: **1,855 passed, 32 subtests passed** in 410.18 seconds. The new tests cover context reservation/budgets, strict line counts and unchanged guards, and ledger write failures/interruption. Fresh historical transfer timing remains the next evaluation; the amended [transfer protocol](triage-natural-transfer-protocol.md) retains its original model, six-slot order and success/time gates.

Evidence: [all development attempts](../reports/triage-v7-development-source-and-count-01.json), [installed command](../reports/triage-v7-installed-01.json), [semantic audit](../reports/triage-v7-development-semantic-audit-01.json). No human study or general time-saving claim has been established.
