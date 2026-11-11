# Decilist docs

New here? Start with the [README](../README.md).

## Using the SDK

| Guide | What it covers |
|---|---|
| [Runtime and direct SDK calls](runtime.md) | Install, run the runtime, call it from Python and TypeScript |
| [Providers](providers.md) | Demo rules, your own function, `LiquidAI/d1-3B`, and what is planned |
| [What Decilist can do](capabilities.md) | Every capability in one table, and where each one works |
| [Named decisions](named-decisions.md) | Define a question once and report on it by name |
| [Verified decisions](verified-decisions.md) | Check ranked choices and use a fallback |
| [Cost-aware decisions](cost-aware-decisions.md) | Choose by the cost of each mistake and replay past decisions |
| [Decision logs and calibration](decision-log.md) | Record outcomes and calculate confidence thresholds |
| [Shadow mode](shadow-mode.md) | Compare model answers with current decisions |
| [Architecture and contracts](architecture.md) | How the pieces fit, and the provider interface |
| [Playground and example app](ui-experience.md) | The optional local pages |
| [Example app walkthrough](demo.md) | The request desk at `/demo` |
| [Evaluation method](evaluation.md) | How to compare providers on your own cases |

## Measured results

| Report | What it covers |
|---|---|
| [Model validation](model-validation.md) | `LiquidAI/d1-3B` on the text benchmark, including failures |
| [Benchmark method](evaluation-v2.md) | How that benchmark was built and frozen |

## Experimental helpers

These helpers are separate from the core SDK. They are experiments, and many of the results below are negative. They stay here so nobody has to repeat them.

| Topic | Start here | Results |
|---|---|---|
| Failing-test triage | [Walkthrough](triage.md), [Arabic guide](install-and-use-ar.md) | [Results](triage-results.md), [verification](triage-verification.md) |
| Natural failure measurement | [Protocol](triage-natural-transfer-protocol.md), [measurement](triage-natural-measurement.md) | [V7 results](triage-natural-v7-results.md) |
| Code review | [Guide](code-review.md) | [Natural results](code-review-natural-results.md), [proof results](code-review-proof-results.md) |
| Review with proof | [Guide](review-proof.md), [protocol](code-review-proof-protocol.md), [holdout protocol](code-review-proof-holdout-protocol.md) | [Proof results](code-review-proof-results.md) |
| Stronger local model | [Experiment](stronger-local.md), [profiles](stronger-local-profiles.md) | |

Each triage version has its own records: [V2 plan](triage-v2-plan-ar.md) and [results](triage-v2-results.md); [V3 experiment](triage-v3-experiment.md) and [results](triage-v3-results.md); [V4 experiment](triage-v4-experiment.md), [quickstart](triage-v4-quickstart.md) and [results](triage-v4-results.md); [V5 experiment](triage-v5-experiment.md), [quickstart](triage-v5-quickstart.md) and [results](triage-v5-results.md); [V6 development](triage-v6-development.md), [experiment](triage-v6-experiment.md), [quickstart](triage-v6-quickstart.md) and [results](triage-v6-results.md); [V7 development](triage-v7-development.md); [V8 development](triage-v8-development.md). Also see the [triage study](triage-study.md) and the [overall verification notes](verification.md).

Commands in these records were written for the runs they describe. Set your own temporary and work folders when you repeat them.
