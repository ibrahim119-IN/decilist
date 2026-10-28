# Proof-first repair: implementation and measured limits

Decilist now has an experimental local JavaScript investigation workflow. It runs existing checks in a disposable copy, requires an actual assertion failure before requesting a small patch, and reruns the identical reproduction and existing checks after the patch. The original project remains unchanged. See the [integration guide](review-proof.md).

Passing these checks establishes a candidate's behavior on those tests. It does **not** establish that the model's expectation belongs to the public contract, that all behavior is preserved, or that developers save time. Candidates need review. The supplied libraries execute with normal Node privileges; this is not an operating-system sandbox.

## First frozen cohort

Six historical defects from p-map, p-timeout, p-some and delay, plus six corrected controls, were assigned once to three arms. All 24 fixture preparation outcomes were verified before exposure. Each arm received complete selected source files, the same public behavior contract and lint diagnostics. Ordinary review proposed a patch without first executing the proposed reproduction; proof-first required a failing assertion; the third arm additionally received the optional d1 specialist's hints. All arms used the same Qwen3-Coder-30B Q4 local generator and post-edit checks.

| Arm | Verified target repairs | Failure-penalized median | Actual time on six defective tasks |
| --- | ---: | ---: | ---: |
| Ordinary local-model review | 0/6 | 180 s | 28.72 s |
| Proof-first | 0/6 | 180 s | 45.00 s |
| Proof-first plus d1 | 0/6 | 180 s | 37.21 s |

Every unsuccessful task counts as the prespecified 180 seconds in the primary metric. Short rejected attempts are not fast repairs. None of the 36 assignments produced an accepted candidate. Most failed before execution because model-quoted indentation differed from the actual cited source; other failures included rejected provider output and edits outside the cited range. This is evidence of a failed integration/evaluation run, not evidence of useful repair or reduced false alarms.

The full [results](../reports/code-review-proof-02/results.json), [failure breakdown](../reports/code-review-proof-02/analysis.json), requests, responses and [freeze](../reports/code-review-proof-02/freeze.json) are retained. The report's `frozen-inputs/` directory preserves the implementation used at that point, because the current implementation subsequently changed. Evidence hashes cover the original bytes.

## Changes after the first cohort

The source quotation matcher now tolerates only leading spaces/tabs on complete quoted lines within the original cited range, then uses the actual source as canonical evidence. It does not repair mismatched tokens, widen line ranges, change source bytes or bypass source hashes. Original provider output remains in the trace. Invalid syntax/imports, arbitrary thrown errors, zero assertions, mutation of tests or source, and timeouts do not count as proof.

A second cohort uses four previously unused projects and the already cached Qwen3.6-27B Q8 model. Both arms use the same stronger generator. Its [protocol](code-review-proof-holdout-protocol.md) was frozen before case exposure. Comparison across these two different cohorts cannot isolate the effect of changing the model.

## Four-project holdout

Four historical defects from p-wait-for, p-debounce, p-memoize and quick-lru were each paired with their upstream corrected version. All 16 fixture preparation outcomes were independently rerun by the primary agent before exposure. Each of the 16 assigned review tasks ran once. Recommended ESLint produced zero diagnostics on these eight selected files; that is not a broad comparison against every lint configuration.

| Arm | Verified target repairs | Failure-penalized median | Actual time on four defective tasks | Corrected controls with an unsupported accepted claim |
| --- | ---: | ---: | ---: | ---: |
| Ordinary local-model review | 0/4 | 180 s | 296.59 s | 1/4 |
| Proof-first | 0/4 | 180 s | 295.62 s | 1/4 |

The prespecified quality, time-saving and false-alarm improvement gate **failed**. The first six-defect cohort also failed; neither is omitted. These two cohorts together cover ten historical defects from eight projects, with 52 assigned tasks, including corrected controls and the first cohort's optional d1 arm.

Twelve holdout tasks reached the 90-second per-request deadline without a completed response. The runtime log shows roughly 20 generated tokens/second, so a response near the configured 2,048-token ceiling could exceed the request budget. This is a generator/workflow budget problem, not an abstention or a successful fast rejection. Both H01 defective-version proposals were rejected because their error formatter accessed `error.constructor.name`, which the execution capability filter forbids; these were recorded under the old coarse `provider_error` code. Those raw proposals are preserved, not rewritten and counted as successes.

Both accepted corrected-version candidates were H01. They added the numeric duration to the default timeout error message. The supplied contract requires a `TimeoutError`, but does not require the numeric duration in its default message. The model's test asserted that extra requirement. In the proof arm, that test really failed before the edit and passed after it, while 27 surrounding checks passed both copies. This demonstrates the central limit: **a real failing test can test an invented requirement**. The finding is adjudicated as unsupported, not a true defect. The same evaluator performed this adjudication after the run; it is not independent or blinded. H03's corrected snapshot has a separately known falsy-rejection defect, so the controls are not asserted to be globally defect-free.

Evidence: [all results](../reports/code-review-proof-03/results.json), [metrics and rejected outcomes](../reports/code-review-proof-03/analysis.json), [control adjudication](../reports/code-review-proof-03/adjudication.json), [freeze](../reports/code-review-proof-03/freeze.json), and exact requests/responses and implementation under `reports/code-review-proof-03/`. A timeout's response file is empty because the non-streaming HTTP request never completed; retained server logs show generation/cancellation. No missing output was invented.

## Post-study corrections

These changes occur **after** the holdout and cannot inherit its freshness. They shorten the generator's output budget, distinguish invalid model output from runtime failure, reject length-truncated responses, and explicitly return `contract_validation: "not_performed"` and `review_required: true`. An accepted candidate remains a proposal for review. Exact frozen implementation snapshots preserve both previous versions.

The installation/example smoke test and any operational replays are labelled separately from fresh accuracy or time measurements. The unresolved product gate is useful, correct repair on a new cohort with fewer unsupported claims. No production auto-apply feature or human time-saving claim is enabled.

Two preselected exposed-case replays completed their responses in 16.13 and 13.21 seconds after the output correction, versus their previous timeout/unsupported-candidate outcomes. Neither produced a repair: H02 quoted an invented source excerpt containing an ellipsis; H01 requested prohibited access in its reproduction. The unchanged guards rejected both. These are [development diagnostics](../reports/code-review-proof-03/post-study-replay/results.json), **not new holdout successes or reduced-false-alarm evidence**.

The final wheel was built, installed into a separate directory, and used through the actual CLI against the local Qwen3.6 server on the authored interval-helper example. The model generated a real failing lower-endpoint assertion, changed only `>` to `>=` in a disposable copy, and passed that assertion plus all four existing tests. The primary agent then checked eight additional endpoint/outside/fractional examples. Original source/tests remained byte-identical. This single 13.54-second run proves installation and workflow operation on a deliberately simple example, not productivity on real repositories. [Installed-wheel evidence and exact requests](../reports/code-review-proof-03/installed-demo/verification.json).

Final focused verification passed **180 tests**. The earlier broader suite had **2,160 passed, 32 subtests passed, and one failure** in an unchanged old 1.5-second process-startup test: it timed out before printing its child-readiness marker under load. Its isolated rerun passed; the assertion was not weakened. The full suite was not repeated after the final output/diagnostic changes. [Verification record](../reports/code-review-proof-03/verification.json).

The ordinary baseline is an automated local-model review workflow, **not a human developer**. Public historical training contamination is unknown. All timing is measured local elapsed time; no human productivity claim follows from these experiments.
