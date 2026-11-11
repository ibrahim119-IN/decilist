# Cost-aware decisions

A confidence threshold treats every mistake as equally bad. Most real decisions are not like that. Ignoring a real security incident is far worse than paging someone for a false alarm, and refusing a good customer is not the same as approving a bad one.

`Costs` lets you say what each mistake costs. The decision then picks the answer with the lowest expected cost, and abstains when asking a person is cheaper than any answer.

Cost-aware decisions are Python only for now. They apply to choices and yes or no questions, not scores.

```python
from decilist import Costs

costs = Costs(
    {
        "ignore": {"page_oncall": 100, "auto_remediate": 5},   # answered ignore, but it was a real incident: 100
        "auto_remediate": {"page_oncall": 40},
        "page_oncall": {"ignore": 3, "auto_remediate": 3},     # paged someone for nothing: 3
    },
    abstain=2,   # sending the alert to an analyst
)

triage = decisions.define("alert_triage", "Choose what to do with this alert.", options=OPTIONS, costs=costs)

answer = await triage(alert)
if answer.status == "accepted":
    act_on(answer.value)
else:
    send_to_analyst(alert)
```

[`examples/cost_aware.py`](../examples/cost_aware.py) is a complete version you can run offline.

## Writing the costs

The table reads `costs[answered][actual]`: the cost of answering `answered` when `actual` was right.

| Part | Meaning | Default |
|---|---|---|
| `wrong[answered][actual]` | Cost of that specific mistake | `default` |
| `default` | Cost of any wrong answer you did not list | `1` |
| A right answer | `wrong[x][x]`, if acting on a right answer still costs something | `0` |
| `abstain` | Cost of asking a person. Required. `None` means never abstain because of cost | |

Costs are finite numbers of at least 0. Use any unit, as long as it is the same one everywhere: money, minutes of work, or plain points. Only the ratios matter. Saying a missed incident costs 100 and a needless page costs 3 means you would accept about 33 needless pages to avoid one miss.

A cost for a label the question does not have raises `ValueError` when you define the decision. A typo in a cost table would otherwise be silent.

For a yes or no question, use `True` and `False`:

```python
fraud = decisions.define("payment_fraud", "Is this payment fraudulent?",
                         costs=Costs({False: {True: 50}}, abstain=10))   # answered no, and it was fraud: 50
```

## How the answer is picked

For each answer, the expected cost is the sum over every possible truth of its probability times the cost of that pair. Take an alert where the model returns `ignore` 0.75, `auto_remediate` 0.05, `page_oncall` 0.20, with the costs above:

| Answer | Expected cost |
|---|---|
| `ignore` | 0.05 × 5 + 0.20 × 100 = 20.25 |
| `auto_remediate` | 0.75 × 1 + 0.20 × 40 = 8.75 |
| `page_oncall` | 0.75 × 3 + 0.05 × 3 = 2.40 |
| Ask a person | 2 |

The most probable answer is `ignore`, at 0.75, and a threshold of 0.7 would accept it. Under these costs it is the most expensive choice, because a one in five chance of a real incident is too much. Asking a person, at 2, is cheaper than the cheapest answer, at 2.40, so the decision abstains.

Ties go to the more probable answer.

## What you get back

A `CostedDecision`:

| Field | Meaning |
|---|---|
| `status` | `accepted`, or `abstained` when asking a person is cheaper or no provider answered |
| `value` | The cheapest answer, or `None` |
| `expected_cost` | The expected cost of `value` |
| `expected_costs` | The expected cost of every answer |
| `abstain_cost` | The `abstain` cost that was used |
| `decision` | The model's own `Decision`. Its `value` is the most probable answer, which can differ from `value` here |
| `request_id` | The same ID as `decision.request_id` |

## The confidence threshold is replaced

With `costs`, `min_confidence` is not applied. The cost rule needs every probability, including the ones for unlikely answers, so the model is asked with a threshold of 0 and `abstain` does the work the threshold did.

The two are the same rule in the simple case. With no cost table, every wrong answer costs 1, and the expected cost of the most probable answer is one minus its probability. So `Costs(abstain=0.3)` accepts exactly when the top probability is at least 0.7, like `min_confidence=0.7`. A cost table generalises that threshold to mistakes that are not equal.

`local_only` and `timeout_ms` still apply. With several providers, a later one is tried only when an earlier one fails, times out or returns invalid output, since low confidence no longer rejects an answer.

## With your own check

`verify` and `costs` work together. The check runs on the answers in order of expected cost, cheapest first, and never tries an answer that costs more than asking a person.

```python
triage = decisions.define("alert_triage", "Choose what to do with this alert.", options=OPTIONS, costs=costs,
                          verify=allowed_by_runbook, fallback=send_to_analyst)
result = await triage(alert)        # a VerifiedDecision
result.costed                       # the CostedDecision behind it
```

If no affordable answer passes, the fallback runs. See [verified decisions](verified-decisions.md).

## Applying costs yourself

`define` is the usual way in. To apply costs to a decision you already have, ask with a threshold of 0 and call `apply_costs`:

```python
from decilist import Policy, apply_costs

decision = await decisions.choose(alert, "Choose what to do with this alert.", OPTIONS, policy=Policy(min_confidence=0))
answer = apply_costs(decision, costs)
```

## What is logged

With a `DecisionLog`, a cost-aware decision writes two lines:

- The usual `decision` line with the model's answer and all its probabilities, under the decision's name.
- A `cost` line with the answer the cost rule picked: `request_id`, `name`, `status`, `value`, `expected_cost` and `abstain_cost`.

The cost table itself is not written to the log.

Record outcomes with the answer that was actually right:

```python
triage.record_outcome(answer, expected="page_oncall")
```

For a decision with costs, `record_outcome` accepts only `expected`. A bare `correct=True` would be ambiguous, because the cost pick and the model's most probable answer can differ.

## Replaying past decisions

Once the log has outcomes, `cost_report` replays the same labelled decisions under two rules and shows what each would have cost:

- The cost rule: the cheapest expected answer.
- The confidence rule: the most probable answer when it reaches `min_confidence`, otherwise abstain.

```sh
decilist cost-report decisions.jsonl --costs costs.json --decision alert_triage --min-confidence 0.7
```

`costs.json` has the same shape as the Python object, and `Costs.to_dict()` writes it:

```json
{
  "wrong": {
    "ignore": {"page_oncall": 100, "auto_remediate": 5},
    "auto_remediate": {"page_oncall": 40},
    "page_oncall": {"ignore": 3, "auto_remediate": 3}
  },
  "default": 1,
  "abstain": 2
}
```

Output from the six made-up alerts in the example:

```text
Replayed 6 of 6 decisions
Rule                      Answered  Abstained  Wrong  Total cost  Mean cost
Cost rule                        4          2      0        4.00      0.667
Confidence >= 0.70               5          1      1      102.00     17.000
Always ask a person: total cost 12.00
Warning: Fewer than 30 decisions could be replayed.
Warning: A replay describes past decisions. The cost rule is only as good as the model's probabilities.
```

These six alerts were written to show how the two rules differ. They do not measure anything, and your own log is the only source for your own numbers.

Add `--json` for the full report, or call it from Python:

```python
from decilist import cost_report

report = cost_report("decisions.jsonl", costs, name="alert_triage", min_confidence=0.7)
report["cost_rule"]["total_cost"], report["confidence_rule"]["total_cost"]
```

The report also gives the cost of always asking a person and the costliest mistakes the cost rule would have made. Because the replay uses stored probabilities, you can try a different cost table on the same log without running the model again.

A decision can be replayed only if its log line has probabilities and its outcome gives `expected`. Decisions made with `costs` always store probabilities. Plain calls store them only when accepted, so run those with `min_confidence=0` if you want to replay them. The report says how many decisions it left out and why.

## Limits

- **The rule trusts the probabilities.** If the model is overconfident, the expected costs are too low. The [model validation report](model-validation.md) records wrong answers from `LiquidAI/d1-3B` with reported probabilities above 0.84. Check a model's probabilities with `decilist calibrate`, and replay real outcomes with `cost-report`, before acting on a cost rule.
- **The costs are yours.** The SDK does not know what a mistake costs your organisation, and a wrong table gives a confidently wrong rule.
- **A replay describes the past.** It covers only the decisions that have outcomes, which may not be a fair sample.
- **Shadow mode does not use costs.** It compares the model's most probable answer with your current one. Define the decision without costs for shadow mode, then use `cost-report` on the labelled log.
- **No scores.** Costs apply to choices and yes or no questions.
- **Python only.** The TypeScript client and the HTTP API do not have costs yet.
