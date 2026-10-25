# Development prompt ablation: 64 existing calls

The completed experiment reused the **eight previously observed v1 regression cases** from [the original machine report](../triage-machine-2026-10-26.json). It tested eight prompt variants once per case: **64 diagnostic calls**, measured at `2026-10-25T21:20:38.262364+00:00`. These are development observations, not held-out evidence or an evaluation of the later enriched assistant.

The original [specification](ablation-spec.json), [results](ablation-results.json), and [work script](original-ablation.py) are preserved byte-for-byte. The original work-script SHA-256 is `db49ca318d905954ccbf54a15004a62bd6faa89a882576d035157c98e0b06f62`. The adapted [reproduction runner](../../benchmarks/triage_prompt_ablation.py) records this source version and hashes its own source, input report, installed original triage definition, and transformed states. [Provenance and hashes](provenance.json) distinguish original measurements from the later tokenizer-only audit.

## Design

The fixed-order **2 × 2 × 2** design varied:

- Evidence: the full prepared-evidence JSON, or readable text that strips blank/separator lines, deduplicates log lines, and appends existing source excerpts. The original readable transform ends with a 6,500-character slice.
- Instructions: the original instructions, or the exact shorter wording retained in the specification under the historical identifier `short_no_hidden_key`.
- Option descriptions: the original six descriptions, or the shorter descriptions retained under `short_direct`. Route keys and their order stay fixed.

Only saved evidence entered the model state. Acceptable-route labels were used afterward for scoring. The original states were 2,607–4,385 characters as JSON and 1,244–1,640 characters as readable text; none of these readable states reached the transform's character cap.

The runtime reported local `LiquidAI/d1-3B`, pinned revision `051bcc464b01b9f92942b364d9586b0ef5912432`. Calls used `local_only=True`, a 30-second timeout, and **diagnostic `min_confidence=0`** to retain route distributions. The raw decision's `status="accepted"` therefore means diagnostic acceptance, not acceptance by the production assistant.

The separate `production_would_suggest` field is a post-hoc comparison: accepted diagnostic result, maximum route probability at least **0.7**, and a route other than `manual_review`. It does not rerun the call at 0.7 or change the production default.

## Observed results

Each row has eight case observations. “Acceptable top route” compares the highest-probability route with the original acceptable-route set, including predictions below 0.7. “Suggestions at 0.7” applies the separate comparison above.

| Evidence | Instructions | Option descriptions | Acceptable top route | Suggestions at 0.7 | Wrong suggestions |
| --- | --- | --- | ---: | ---: | ---: |
| JSON | Original | Original | 6/8 | 1/8 | 0 |
| JSON | Original | Short | 5/8 | 2/8 | 0 |
| JSON | Short | Original | 7/8 | 1/8 | 0 |
| JSON | Short | Short | 5/8 | 1/8 | 0 |
| Readable, deduplicated | Original | Original | 7/8 | 1/8 | 0 |
| Readable, deduplicated | Original | Short | 3/8 | 1/8 | 0 |
| Readable, deduplicated | Short | Original | 7/8 | 1/8 | 0 |
| Readable, deduplicated | Short | Short | 3/8 | 1/8 | 0 |

Readable formatting alone changed acceptable top routes from 6/8 to 7/8 in this reused sample, while the 0.7 comparison remained 1/8. Other combinations reduced acceptable top routes. These observations do **not** establish that formatting fixed the model, calibrated confidence, or improved new-case performance. Zero wrong suggestions among only one or two suggestions per variant is too little evidence for a reliability claim.

The 64 calls repeatedly use the same eight cases; they are not 64 independent examples. There was one observation per cell/case, no randomized run order, no uncertainty intervals, and no human repair trial. Fixed ordering and warm-up effects also prevent treating the recorded elapsed times as a controlled speed comparison. Cost, repair success, and developer time saved were not measured here.

## Prompt-length and probability caveats

The original tokenizer audit existed as console output. The retained [audit JSON](tokenizer-audit.json) is a later, read-only rerun of the reviewed [tokenizer/pure-renderer script](tokenizer-audit.py), using cached assets; **the 64 inference calls were not rerun**. It renders the original JSON construction with the original instructions/options. Across the eight regression cases, it reports **986–1,495 prompt tokens**, below the pinned configuration's **32,768-token** context setting, and 5–13 signal lines repeated in the log. It also records the A–F option aliases. The audit's two control cases are excluded from that regression token range.

This audit does not record runtime token usage for all eight variants, establish other implementation limits, or prove answer quality. Character counts and character clipping are not token counts or model context limits. The readable variants' prompt-token counts were not measured by this audit. Its code reads a cached tokenizer and pure prompt renderer, not model weights; it is not an OS-level network capture.

The retained probabilities describe the model's option readout distribution. They are not calibrated probabilities that a next check, diagnosis, or repair is correct. The highest route can be acceptable while still falling below the unchanged production threshold.

## Reproduce without overwriting measurements

Install the SDK and use the original frozen triage definition referenced by the input report. From the repository root, this preparation command checks the eight-case selection and writes 64 transformed-state hashes, with **no network or model calls**:

```powershell
python -m benchmarks.triage_prompt_ablation --previous-report reports/triage-machine-2026-10-26.json --output-dir reports/local/ablation-prepare-new --prepare-only
```

To deliberately run a new diagnostic experiment, first make the pinned learned local runtime ready as described in [runtime setup](../../docs/runtime.md), then use a different new directory:

```powershell
python -m benchmarks.triage_prompt_ablation --previous-report reports/triage-machine-2026-10-26.json --output-dir reports/local/ablation-rerun-new --url http://127.0.0.1:8765
```

The runner refuses existing output directories, remote endpoints, alternate models/revisions, and a changed original triage definition. A partial run is marked `incomplete.json`; it does not replace completed results. New inference can produce different distributions or timing and remains development-only reuse of these old cases.

Offline verification of the adapted runner matched **all 64 original state hashes and character counts**, compared the readable transform with the original function, verified the eight variants and short constants, and confirmed an existing output directory is rejected without changing its files. Syntax checks passed. This verifies reproduction mechanics, not a new inference outcome.
