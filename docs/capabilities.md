# What Decilist can do

Every capability in one place, with where it works and where to read more. Use this page to see the whole SDK at a glance.

The columns say where a capability is available today: in the Python SDK, in the TypeScript SDK, and over plain HTTP.

## Asking a question

| Capability | What it gives you | Python | TypeScript | HTTP | Guide |
|---|---|:-:|:-:|:-:|---|
| `choose` | One of up to 64 named options | Yes | Yes | Yes | [README](../README.md#how-a-decision-runs) |
| `predict` | A yes or no answer | Yes | Yes | Yes | [README](../README.md#how-a-decision-runs) |
| `score` | A number on 2 to 10 ordered levels | Yes | Yes | Yes | [README](../README.md#how-a-decision-runs) |
| Structured state | Pass a dictionary or list as the facts, not only text | Yes | Yes | Text | [Runtime](runtime.md) |
| Sync calls | `choose_sync`, `predict_sync`, `score_sync` for scripts | Yes | No | No | [README](../README.md#connect-it-to-your-project) |
| Named decisions | Define a question once and call it with only the state | Yes | No | No | [Named decisions](named-decisions.md) |

## Staying safe

| Capability | What it gives you | Python | TypeScript | HTTP | Guide |
|---|---|:-:|:-:|:-:|---|
| Abstention | A clear "no answer" instead of a guess, below the confidence you set | Yes | Yes | Yes | [README](../README.md#how-a-decision-runs) |
| Local only | A remote provider is blocked before it sees any data. On by default | Yes | Yes | Yes | [Architecture](architecture.md) |
| Time limit | Each provider gets `timeout_ms`, then the next one is tried | Yes | Yes | Yes | [Architecture](architecture.md) |
| Attempt trace | Every result lists what was tried and why it was rejected | Yes | Yes | Yes | [Architecture](architecture.md) |
| Verified choices | Your own check runs on the ranked answers, with a fallback | Yes | Yes | No | [Verified decisions](verified-decisions.md) |
| Cost-aware decisions | Choose by the cost of each mistake, and abstain when a person is cheaper | Yes | Yes | By hand | [Cost-aware decisions](cost-aware-decisions.md) |

## Learning from results

| Capability | What it gives you | Python | TypeScript | HTTP | Guide |
|---|---|:-:|:-:|:-:|---|
| Decision log | A local, append-only record of each decision and its attempts | Yes | No | No | [Decision logs](decision-log.md) |
| Outcomes | Record what turned out to be right for a decision | Yes | No | No | [Decision logs](decision-log.md) |
| Calibration | A threshold table and a recommended threshold from your own outcomes | Yes | No | No | [Decision logs](decision-log.md) |
| Reports per decision | Calibrate and report on one named decision | Yes | No | No | [Named decisions](named-decisions.md) |
| Shadow mode | Run the model, or a cost rule over it, beside your current answers without acting on it | Yes | No | No | [Shadow mode](shadow-mode.md) |
| Cost replay | Replay past decisions under a cost rule and a confidence rule | Yes | No | No | [Cost-aware decisions](cost-aware-decisions.md) |
| Evaluation | Compare providers on your own labelled cases | Yes | No | No | [Evaluation method](evaluation.md) |

## Bringing a model

| Provider | What it is | Guide |
|---|---|---|
| `FunctionProvider` | Any local function that returns probabilities: rules or your own model | [Providers](providers.md) |
| `PredictProbaProvider` | An estimator with `classes_` and `predict_proba`, such as a scikit-learn model | [Providers](providers.md) |
| `LlamaCppProvider` | A llama.cpp server you already run locally | [Providers](providers.md) |
| `LiquidLocalProvider` | `LiquidAI/d1-3B` on your own machine, a separate download | [Providers](providers.md), [validation](model-validation.md) |
| `UniformProvider` | An uninformative baseline for comparisons | [Providers](providers.md) |
| Demo rules | Fixed rules for one example workflow, to test your wiring | [README](../README.md#whats-in-the-box) |
| OpenAI Decisions | Planned, not built | [Providers](providers.md) |

## Running it

| Capability | What it gives you | Guide |
|---|---|---|
| In-process calls | Use the SDK inside your Python app with no server | [README](../README.md#connect-it-to-your-project) |
| Local runtime | A loopback HTTP service for TypeScript and other languages | [Runtime](runtime.md) |
| One-command launcher | Start your app and a runtime together with `decilist runtime run` | [Runtime](runtime.md) |
| Playground | Try questions in the browser and copy the code | [Playground](ui-experience.md) |
| Example app | A small ordinary app that uses a decision behind the scenes | [Walkthrough](demo.md) |

## Command line

| Command | What it does |
|---|---|
| `decilist runtime setup`, `start`, `run`, `status` | Download a model on request, run the local runtime, check it |
| `decilist calibrate LOG` | Threshold table and recommendation from a decision log |
| `decilist shadow-report LOG` | Agreement, coverage and latency from shadow comparisons |
| `decilist cost-report LOG --costs FILE` | Replay labelled decisions under a cost rule and a confidence rule |

`calibrate`, `shadow-report` and `cost-report` accept `--decision NAME` to report on one named decision, and `--json` for machine-readable output.

## What it does not do

- It does not include a model. The demo rules are not AI, and real decisions need a model you download or bring.
- It does not make a model more accurate. It controls when an answer is used, checked or handed to a person.
- Confidence is the model's own estimate. Nothing here guarantees that an accepted answer is right.
- The log, shadow mode, named decisions and cost replay are Python only for now, because they need the decision log. The TypeScript client and the runtime do not log yet.

The measured quality of `LiquidAI/d1-3B`, including its failures, is in the [model validation report](model-validation.md). Changes are listed in the [changelog](../CHANGELOG.md).
