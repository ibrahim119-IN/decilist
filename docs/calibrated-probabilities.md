# Calibrated probabilities

A model's confidence is its own estimate, and models are often too sure of themselves. If a model says 0.95 and is right 70% of the time, a threshold of 0.9 lets through far more mistakes than you meant, and a [cost rule](cost-aware-decisions.md) computes expected costs that are too low.

`fit_calibration` learns a correction from your own logged outcomes, so that a stated 0.9 is right about nine times in ten. You then attach it to a decision, and every probability is corrected before the threshold or the cost rule sees it.

Fitting needs the Python decision log. Applying a fitted calibration works in Python and TypeScript, and by hand over plain HTTP. It applies to choices and yes or no questions, not scores.

```python
from decilist import Calibration, fit_calibration

fit = fit_calibration("decisions.jsonl", name="alert_triage")
fit.calibration.save("alert_triage.calibration.json")

triage = decisions.define(
    "alert_triage",
    "Choose what to do with this alert.",
    options=OPTIONS,
    costs=costs,
    calibration=Calibration.load("alert_triage.calibration.json"),
)
```

[`examples/calibration.py`](../examples/calibration.py) is a complete version you can run offline.

## What calibration does and does not do

The correction is one number, a temperature. Above 1 it makes the model less sure of itself, below 1 more sure, and at 1 it changes nothing. With the temperature of 2.57 fitted in the example, a three-way choice is corrected like this:

| The model says | Corrected |
|---|---|
| 0.99 | 0.80 |
| 0.90 | 0.61 |
| 0.70 | 0.48 |

It never changes which answer is the most probable, and it keeps the order of the answers. So:

- **It does not make the model more accurate.** The same answers are right and wrong as before.
- **It makes the numbers mean something.** A threshold of 0.8 now accepts answers that are right about 80% of the time.
- **It changes what a cost rule picks.** Expected costs use the probabilities themselves, so a corrected 0.80 can send an alert to a person where an uncorrected 0.99 would have ignored it.

## Step 1: log decisions with outcomes

A fit uses choose or predict decisions that have stored probabilities and an outcome. Define the decision, run it, and record what turned out to be right:

```python
answer = await triage(alert)
...
triage.record_outcome(answer, expected="page_oncall")
```

Run with `min_confidence=0`, or define the decision with costs, while you collect outcomes. An abstention stores no probabilities, so a log made with a threshold above 0 is missing exactly the less confident answers the fit needs, and the fit warns about it. [Shadow mode](shadow-mode.md) is a good way to collect outcomes without acting on the model.

Prefer `expected` to `correct`. For a choice, a bare `correct=False` does not say which answer was right, so that decision cannot be used.

## Step 2: fit

```sh
decilist fit-calibration decisions.jsonl --decision alert_triage --output alert_triage.calibration.json
```

Output from the 100 made-up alerts in the example, where a stand-in says 0.99 or 0.90 and is right less often than that:

```text
Fitted on 100 of 100 decisions, model stand-in-v1
Temperature: 2.569 (above 1: the model was too sure)
                 Mean confidence  Accuracy  Log loss   Brier     ECE
Before                     0.945     0.700     1.165   0.545   0.245
After (5-fold)             0.702     0.700     0.797   0.451   0.045
Wrote alert_triage.calibration.json
Warning: A calibration describes past decisions. Fit it again when the model, the question or the data changes.
```

These alerts were written to show the mechanism. They do not measure anything.

How to read it:

- **Mean confidence against accuracy** is the headline. Before, the stand-in said 0.945 on average and was right 0.700 of the time. After, it says 0.702.
- **Accuracy does not move**, because the most probable answer never changes.
- **Log loss, Brier and ECE** are three standard measures of how well probabilities match outcomes. Lower is better. ECE is the average gap between confidence and accuracy over ten confidence bands.
- **The "After" row is cross-validated.** Each decision is scored with a temperature fitted on the other folds, not on itself, so the row is not flattered by seeing its own answers.

If the cross-validated log loss does not go down, the calibration is written with temperature 1 and a warning. It then leaves the probabilities alone, so a model that is already well calibrated is not disturbed.

In Python:

```python
fit = fit_calibration("decisions.jsonl", name="alert_triage", model=None, target_accuracy=None, min_samples=30, folds=5)
fit.calibration          # a Calibration, or None when there was too little to fit on
fit.report["before"]     # mean_confidence, accuracy, log_loss, brier, ece
fit.report["after"]
fit.report["helps"]      # whether the cross-validated log loss went down
fit.report["warnings"]
```

Add `--json` to the command for the same report.

### What a fit needs

| Requirement | Why |
|---|---|
| One question | Pass `--decision NAME`. Decisions with different answers cannot share a fit, and mixing choose and predict is refused |
| One model | A calibration belongs to the model it was fitted on. With several models in the log, pass `--model` |
| At least 30 usable decisions | Set with `--min-samples`. Under 100, the fit warns that the temperature is a rough estimate |
| Stored probabilities and the right answer | Decisions without them are left out, and the report says how many |

The temperature is kept between 0.25 and 10. A fit that lands on either edge warns that one number may not be enough to correct this model.

## Step 3: use it

Attach the calibration to a named decision:

```python
calibration = Calibration.load("alert_triage.calibration.json")
triage = decisions.define("alert_triage", "Choose what to do with this alert.", options=OPTIONS, calibration=calibration)
answer = await triage(alert)
answer.probabilities     # corrected
answer.confidence        # corrected, and compared with the threshold
```

The probabilities are corrected inside the engine, before the confidence threshold. Everything after that uses the corrected numbers:

| With | What changes |
|---|---|
| A threshold | `min_confidence` is compared with the corrected confidence |
| [Costs](cost-aware-decisions.md) | Expected costs are computed from the corrected probabilities |
| [Verify](verified-decisions.md) | The order is unchanged without costs, and follows corrected costs with them |
| [Shadow mode](shadow-mode.md) | `triage.observe` and `triage.compare` run with the calibration |
| A remote runtime through `Client` | The runtime is asked for every answer, and the correction and the threshold are applied on the client |

A calibration is refused when the decision is defined if it was fitted for a different kind of question or for different answers. It is applied only to answers from the model it was fitted on. An answer from another provider in the list is left as it is.

The low-level form is `decisions.decide(question, policy, calibration=calibration)`. To correct a decision you already have, use `calibrate_decision(decision, calibration, min_confidence=0.8)` on a decision asked with `min_confidence=0`.

## A threshold for a target accuracy

Add a target and the fit also chooses a threshold on the corrected confidence, and stores it in the calibration:

```sh
decilist fit-calibration decisions.jsonl --decision alert_triage --target-accuracy 0.95 --output alert_triage.calibration.json
```

It is the lowest threshold whose accepted answers have a 95% Wilson lower bound on accuracy at or above the target, with at least `--min-samples` labelled answers. This is the same rule as [`decilist calibrate`](decision-log.md). If no threshold qualifies, none is stored and the output says so.

A decision defined with that calibration uses the stored threshold:

```python
triage = decisions.define("alert_triage", ..., calibration=calibration)                                      # the calibration's threshold
triage = decisions.define("alert_triage", ..., calibration=calibration, policy=Policy(min_confidence=0.9))  # your policy wins
```

The order is: a policy passed to the call, then the definition's policy, then the calibration's threshold, then the policy of the `decisions` object. With costs there is no threshold at all, as before.

The threshold is chosen on the same decisions the temperature was fitted on, so its accuracy is somewhat optimistic. It is a recommendation from past data, not a guarantee.

## The calibration file

```json
{
  "calibration_version": 1,
  "method": "temperature",
  "temperature": 2.569490549285349,
  "kind": "choose",
  "labels": ["auto_remediate", "ignore", "page_oncall"],
  "model": "stand-in-v1",
  "name": "alert_triage",
  "min_confidence": null,
  "target_accuracy": null,
  "fitted_on": 100,
  "created": "2026-11-11T12:00:00+00:00"
}
```

It holds no decisions, states or instructions. Keep it next to your code and replace it when you fit again.

## What is logged

A decision that was corrected stores the corrected probabilities and a `temperature` field. A later fit uses that field to recover the model's own probabilities, so you can keep the calibration switched on and still fit again from the same log. The new temperature replaces the old one. It is not applied on top of it.

## TypeScript

The TypeScript SDK applies a calibration file with the same arithmetic, and a test in each SDK checks the same figures.

```typescript
import calibration from "./alert_triage.calibration.json" with {type: "json"};

const answer = await decisions.chooseCalibrated(alert, "Choose what to do with this alert.", OPTIONS, {calibration});
const costed = await decisions.chooseCosted(alert, "Choose what to do with this alert.", OPTIONS, {costs, calibration});
```

| Python | TypeScript |
|---|---|
| `define(..., options=..., calibration=c)` | `chooseCalibrated(state, instructions, options, {calibration, policy})` |
| `define(..., calibration=c)` for yes or no | `predictCalibrated(state, instructions, {calibration, policy})` |
| `define(..., costs=..., calibration=c)` | `chooseCosted` and `predictCosted` with `{costs, calibration}` |
| `define(..., verify=..., calibration=c)` | `chooseVerified` with `{verify, calibration}` |
| `calibration.apply(probabilities)` | `applyCalibration(probabilities, calibration)` |
| `calibrate_decision(decision, c, min_confidence=x)` | `calibrateDecision(decision, calibration, x)` |

Without a policy in the call, `chooseCalibrated` uses the calibration's own threshold when it has one, and otherwise the client's policy. Fitting is Python only, because the TypeScript SDK has no decision log.

## Over plain HTTP

Send the question with `"min_confidence": 0`. Raise each returned probability to the power of one over the temperature, then divide each result by their sum. Apply your threshold or cost rule to those numbers.

## Limits

- **It needs outcomes that are a fair sample.** If only the easy decisions get labelled, the fit describes the easy decisions.
- **One number cannot fix everything.** A temperature corrects a model that is evenly too sure or too cautious. It cannot fix a model that is too sure about one answer and too cautious about another.
- **It belongs to one model, one question and one kind of data.** Fit again when any of them changes. Nothing here detects that the data has drifted.
- **Small logs give rough temperatures.** The fit refuses under 30 usable decisions and warns under 100.
- **Through `Client`, low confidence no longer moves to the next provider.** The runtime is asked with a threshold of 0, so a later provider is tried only when an earlier one fails. In-process, the engine corrects each provider's answer before the threshold, and the next provider is still tried.
- **No scores.** A score's value is an expected level, and a temperature would change it.
- **Not a guarantee.** A corrected 0.9 is right about nine times in ten on the data it was fitted on. Confidence remains an estimate.
