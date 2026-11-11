# Decision logs and calibration

The Python SDK can keep an append-only JSONL log on your own disk. This is Python only for now: the TypeScript client and runtime server do not log. The directory must already exist. The file is created on the first write; use one process per file. There is no rotation or upload.

```python
from decilist import Decilist, DecisionLog, Policy

log = DecisionLog("./decisions.jsonl")
decisions = Decilist(providers, Policy(min_confidence=0), log=log)
decision = await decisions.choose("Order details", "Choose an action", {"ship": "Ship", "hold": "Hold"})
log.record_outcome(decision.request_id, expected="ship")
# Alternatively: log.record_outcome(decision.request_id, correct=True)
```

You can also pass `log=log` to `Client` or `Client.connect`. Each decision records its result, confidence, probabilities, provider, model, timing, attempts, policy threshold, and SHA-256 hashes of state and instructions. State is omitted unless `DecisionLog(..., include_state=True)` is used. Instructions and option descriptions are never stored as input fields. Decision values and probability keys contain option labels, which you should choose accordingly. A hash is not encryption: short, guessable state can be recovered by trying values.

A decision made through a [named decision](named-decisions.md) also records its `name`. A decision defined with [costs](cost-aware-decisions.md) writes a second `cost` line with the answer the cost rule picked; the cost table itself is not stored.

Outcomes are appended separately; provide exactly one of `correct` or `expected`. If an outcome is recorded more than once, the last one is used. When `choose_verified` is used, the first normal check of an accepted decision is recorded automatically; its `correct` means "passed your check". A check that raises does not provide a label.

```sh
decilist calibrate decisions.jsonl --target-accuracy 0.9 --min-samples 30
decilist calibrate decisions.jsonl --model MODEL_NAME --json
decilist calibrate decisions.jsonl --decision alert_triage --target-accuracy 0.9
```

A log that holds several questions cannot be calibrated as one. `--decision NAME` (or `name=` on `threshold_table`) limits the table to one named decision. Without it, a log with more than one named decision gets a warning that lists them, and the result's `names` field lists them too.

Each row shows the fraction of decisions covered at a threshold, the number with outcomes, their accuracy, and a 95% Wilson lower bound. The recommendation is the lowest threshold whose lower bound meets the target with enough labelled samples. Reliability bins compare mean confidence with observed accuracy. Scores are excluded. Use `min_confidence=0` for a while to see the full table: abstentions carry no probabilities, so lower thresholds cannot be reconstructed from them. Fallbacks and mixed questions or models also limit interpretation; check the warnings.

Filtering by model includes only decisions that identify that model. Abstentions without a model are excluded, so the filtered coverage does not include them. For evaluation cases, where no policy threshold is recorded, the table treats the logged minimum as zero. Run those cases with `min_confidence=0` to interpret the full table.

This data comes from your application's own runs. A table describes the past data it was built from, and a recommended threshold is not a guarantee. Outcomes must be representative of the decisions for the accuracy estimates to be useful.
