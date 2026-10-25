# Bounded text evaluation v2

The `request-text-v1` suite adds 60 authored English cases for a small text-composer integration. It evaluates a single declared rubric, not general intelligence. Codex authored the cases and labels; they have not received independent human label review. Learned model performance remains unmeasured until an actual Liquid report is produced. The old 12-case `workflow-smoke-v1` dataset, runner, and existing reports remain separate wiring evidence.

The hypothetical app receives a request, optional existing app context, and source text. It supports exactly one action: summarize, proofread, or shorten. It needs nonempty available source and exactly one destination: note, message, or document. The allowed synonyms and the distinction between missing information and conflicting intent are explicit in the shared task instructions. This is deliberately narrower than arbitrary request understanding. The SDK continues to accept application-defined questions; this rubric belongs to this benchmark.

## Ground truth and frozen split

`benchmarks/text_requests.json` contains 20 development and 40 held-out cases. Each row has an ID, operation, split, authored expected answer, rationale, and tags. The split sizes by operation are:

| Split | choose | predict | score | Total |
| --- | ---: | ---: | ---: | ---: |
| Development | 7 | 7 | 6 | 20 |
| Held-out | 14 | 13 | 13 | 40 |

Both splits contain complete requests, missing fields, ambiguity, unsupported/unexpected requests, paraphrases, and instructions embedded in evidence. Paired paraphrases stay inside their own split. Some adversarial source text impersonates a system role or demands a specific route, boolean, or score. These are synthetic probes, not a comprehensive prompt-injection safety test.

Ground truth is explicit:

- `choose`: `proceed` when all fields are clear; `clarify` for absent required information; `review` for unsupported, ambiguous, multiple-action, or contradictory intent. Review takes precedence over absence.
- `predict`: true only when the complete request is ready to proceed under that same rubric; otherwise false.
- `score`: the count of independently valid action, source, and destination fields, from zero to three. An invalid action does not remove points for a valid source or destination. This measures field completeness, not confidence or safety.

The provider receives only the task definition and authored input. IDs, splits, tags, expected answers, and label rationales are not added to its question. A task definition necessarily explains the labeling rubric; it must not contain case-specific answers. Labels are stored for scoring and inspection, not hidden from repository readers.

`benchmarks/text_freeze.json` pins exact dataset and policy SHA-256 values and ordered membership. The runner rejects modifications that do not match it. This preserves a declared artifact, not a cryptographic proof that a developer never saw test data. The policy is local-only, minimum probability 0.7, and soft timeout 30 seconds. It was fixed before learned-model outputs, without calibration. No policy search or held-out tuning is performed. Any later changes to labels, prompts, or thresholds require a separately disclosed dataset/policy version; do not rewrite this manifest to improve an existing held-out result. Development and held-out results are reported separately.

## Run

From the repository root in the environment where Decilist is installed:

```powershell
python -m benchmarks.run_text --provider uniform --split all --repetitions 3 --output reports/local/request-text-uniform.json
python -m benchmarks.run_text --provider liquid --allow-model-code --device cuda --split all --repetitions 3 --output reports/local/request-text-liquid.json
```

Model setup is a separate explicit step. The Liquid command uses `LiquidLocalProvider.load` once, requests offline loading, and records its model ID and exact adapter revision. The current pinned revision is `051bcc464b01b9f92942b364d9586b0ef5912432` of `LiquidAI/d1-3B`. It does not download missing weights. `--allow-model-code` is the explicit permission to run the already reviewed vendor code. Imports do not load the model. `--split dev` permits an initial development run; a final `--split heldout` uses the identical frozen policy. Use a new output filename for each run: the runner refuses to overwrite evidence.

The model is loaded once per process. Three warmups, cycling through all operations, use a separate input outside the 60 cases. Warmup outputs and times are recorded separately. `--warmup 0` is available for diagnosing first calls, but then scored calls must not be described as fully warmed. `cold_provider_load_ms` includes imports and loading in that process; it is not a clean-machine cold-start measurement because filesystem caches are not flushed. A repeat count of one is useful for smoke verification, while three repeats yields 60 development and 120 held-out latency observations. Repeated calls do not create new independent test cases or establish statistical confidence.

The runner emits brief progress every ten scored calls and a summary when each split completes. Native GPU work has a soft timeout: an expired request may keep running and retain the provider lock. Later lock-wait timeouts remain in the report. This runner does not claim hard cancellation or concurrent throughput.

Only `uniform` and `liquid` are offered here. The existing `workflow-rules` consumes complete/valid/transient workflow fields and cannot interpret these text requests. Applying it to this suite would misrepresent its support, so the report marks it unsupported instead of inventing a comparable score. Use `python -m benchmarks.run` for the original rules/uniform/fallback wiring check. Uniform probabilities are deterministic; under the frozen 0.7 policy, this suite's distributions all abstain. Its zero coverage is expected and is not model-quality evidence.

## Metrics and report interpretation

`decilist.evaluation.summarize` validates cases and final decisions, and reports:

- `accuracy_all`: correct accepted choose/predict decisions divided by all choose/predict cases. Abstentions are misses. Score cases are excluded from this denominator.
- `accuracy_accepted`: correct accepted choose/predict decisions divided by accepted choose/predict decisions; null if none are accepted.
- `coverage`: accepted decisions of any operation divided by all scored calls, and `abstention_rate` as its complement. Both are independent of correctness.
- `score_mae`: mean absolute error of accepted numeric scores against the zero-to-three ground truth. It is null if no score is accepted. `score_coverage`, `score_cases`, and `score_accepted` are always alongside it; abstentions are never assigned an invented numeric error.
- `valid_output_rate`: accepted or low-confidence provider attempts divided by all attempted provider calls, including timeouts/errors/invalid output and excluding policy-blocked providers. This is a valid distribution received within the timeout, not correctness. Low confidence may therefore give valid output but zero coverage. Invalid-output, timeout, error, blocked, and low-confidence counts are also separate.
- `fallback_cases`, `fallback_rate`, and `fallback_successes`: requests reaching more than one configured provider, divided by calls where applicable, and accepted such requests. This runner uses a single provider, so these are zero; the evaluation tests exercise actual fallback counting.
- Warm sequential `p50_ms` and `p95_ms`, using nearest-rank order statistics over the stated scored repetitions. Per-operation and per-split metrics prevent the mixed operation total from hiding a failing operation. Errors list all abstentions/mismatches, with score absolute error only for accepted numeric outputs.

All individual outcomes remain in the report with original ID, repetition, expected label, tags, value/probabilities, status, provider/model, latency, and attempt outcomes. Numeric score values are probability-weighted rubric indices from the existing SDK contract; they need not be integers. MAE preserves this continuous value without rounding it into a fake classification answer.

Reports contain timestamp, dataset/freeze/policy hashes, model/revision, requested device, Python and available library versions, OS/CPU, Git revision/dirty state when available, CUDA/GPU metadata when loaded, and process/allocator memory observations when accessible. A dirty Git revision does not identify uncommitted code fully; retain the exact candidate checkout as well as its dataset hashes. Unavailable fields are null. CUDA allocator peaks exclude allocations outside PyTorch; process RSS is not whole-system peak memory. Cost, energy, calibration, network observation, and concurrent throughput remain explicitly unmeasured/null. Requesting offline loading is not network-observation evidence. Vendor latency claims are never substituted for local measurements.

The test suite validates malformed labels and IDs, mixed metric denominators, score MAE and score abstention, classification abstention, invalid final responses, real fallback attempt counting, dataset/split checks, one-time model loading, separate warmup, repetitions, and evidence preservation. A fake provider in runner tests is marked as a test fixture and is never presented as Liquid inference.
