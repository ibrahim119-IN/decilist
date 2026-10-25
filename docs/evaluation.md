# Evaluation methodology

The initial `workflow-smoke-v1` suite checks a generic state machine: proceed when complete and valid, retry a transient problem with fewer than two attempts and a nonnegative integer count, otherwise review. There are 12 synthetic cases, including missing fields, a string pretending to be a boolean, exhausted retries, and an instruction embedded in evidence.

This dataset is authored specifically for the example. The rules encode its labeling policy. A perfect rule score is a wiring check, not learned reasoning, an independent benchmark, or proof of prompt-injection resistance in a model. Uniform probabilities use the first declared option for ties; this is an uninformative deterministic baseline, not random sampling.

The report records the dataset SHA-256, timestamp, provider/model IDs, policy, individual outputs, and warm sequential latencies after one warmup. Model load time is excluded. Metrics:

- `accuracy_all`: correct accepted decisions / all cases. Abstentions count as misses.
- `coverage`: accepted decisions / all cases.
- `accuracy_accepted`: correct / accepted, or null when none are accepted.
- `abstention_rate`, invalid-output attempts, and timeout attempts.
- p50/p95 latency by nearest-rank order statistic. Twelve observations are too few for credible production tail-latency claims.
- Cost remains null until measured with documented assumptions. No extrapolated “million decisions” savings are reported.

The first runner supports classification and predicate labels. Score reporting with MAE, rubric agreement, confidence calibration/Brier score, throughput under concurrency, and token/energy accounting is not implemented.

Before the presentation, add a separately reviewed held-out dataset with text paraphrases and ambiguity. Keep tuning and final test cases separate. Compare providers on identical inputs and policies; also show quality vs coverage across thresholds. Report machine, runtime versions, warmup, repetitions, cold start, memory, failures, and whether network access occurred. Do not present vendor latency numbers as measurements from this repository.
