# Named decisions

`decisions.define` gives one question a name. You write the instructions, the allowed answers, the threshold and the check once, and call the result with only the state. The name goes into every log line that decision produces, so the reports can describe one decision instead of the whole log.

Named decisions are Python only for now. The TypeScript client and the HTTP API do not carry a name.

```python
from decilist import Decilist, DecisionLog, Policy

log = DecisionLog("./decisions.jsonl")
decisions = Decilist(providers, log=log)

triage = decisions.define(
    "alert_triage",
    "Choose what to do with this alert.",
    options={"ignore": "Known noise", "auto_remediate": "An approved fix exists", "page_oncall": "Needs a person now"},
    policy=Policy(min_confidence=0.8),
)

answer = await triage(alert)
if answer.status == "accepted":
    act_on(answer.value)
else:
    send_to_analyst(alert)
```

[`examples/named_decision.py`](../examples/named_decision.py) is a complete version you can run offline.

## Why define a decision

Without a name, each `choose` call stands alone. A log that holds several questions cannot be calibrated as one: `decilist calibrate` warns that "this table mixes different questions", and one threshold for all of them fits none of them.

With a name:

- The question is written in one place, so two call sites cannot drift apart.
- Each decision has its own policy, and so its own confidence threshold.
- `decilist calibrate` and `decilist shadow-report` can report on one decision with `--decision`.
- Outcomes are recorded through the definition, next to the call that produced them.

## The three kinds

The arguments decide the kind, the same way the direct calls do.

| You pass | Kind | Same as | Value |
|---|---|---|---|
| `options` | Choice | `choose` | The option's name |
| `levels` | Score | `score` | A number, the expected level |
| Neither | Yes or no | `predict` | `True` or `False` |

```python
ready = decisions.define("release_ready", "Is this release ready to ship?")
quality = decisions.define("report_quality", "Rate this report.", levels=["poor", "fair", "good"])
```

`options` accepts a dictionary or an `Enum` class, like `choose`. Passing both `options` and `levels` is an error.

## Arguments

| Argument | Meaning |
|---|---|
| `name` | 1 to 64 letters, digits, `_`, `.` or `-`, starting with a letter or digit. Unique on its `decisions` object |
| `instructions` | The question, as you would pass it to `choose`, `predict` or `score` |
| `options`, `levels` | The allowed answers, which also set the kind |
| `policy` | This decision's own `Policy`. Defaults to the policy of the `decisions` object |
| `verify`, `fallback`, `max_tries` | An application check on the ranked answers, as in [verified decisions](verified-decisions.md). Choices only |
| `costs` | Choose by the cost of each mistake. See [cost-aware decisions](cost-aware-decisions.md). Choices and yes or no |

The definition is checked when you define it, before any model runs. A bad name, a second definition with the same name, a single option, empty instructions, `verify` on a yes or no question, or a cost for a label the question does not have all raise `ValueError` at that point.

`decisions.definitions` returns the defined decisions by name.

## Calling it

```python
answer = await triage(alert)                                   # async
answer = triage.sync(alert)                                    # scripts; needs a Decilist, not a Client
answer = await triage(alert, policy=Policy(timeout_ms=30000))  # a policy for this one call
```

`state` is a string, a dictionary, a list or a tuple, as in the direct calls.

What you get back follows the definition:

| Definition | Result | Statuses |
|---|---|---|
| Plain | `Decision` | `accepted`, `abstained` |
| With `verify` | `VerifiedDecision` | `verified`, `fallback`, `unresolved` |
| With `costs` | `CostedDecision` | `accepted`, `abstained` |
| With `costs` and `verify` | `VerifiedDecision`, with the cost result in `.costed` | `verified`, `fallback`, `unresolved` |

When a definition has `verify`, calling it always runs the check. There is no way to call a checked decision and skip its check.

## Recording outcomes

Pass the result back with what turned out to be right. This needs a `DecisionLog` on the `decisions` object.

```python
answer = await triage(alert)
...
triage.record_outcome(answer, expected="page_oncall")   # what was actually right
triage.record_outcome(answer, correct=True)             # or only whether the answer was right
```

You can pass a request ID string instead of the result. Provide exactly one of `expected` and `correct`. Prefer `expected`: it lets the log be replayed under other rules later, which `correct` cannot do.

## Reports for one decision

A decision line in the log gains a `name` field. Lines from direct calls have no such field.

```sh
decilist calibrate decisions.jsonl --decision alert_triage --target-accuracy 0.95
decilist shadow-report decisions.jsonl --decision alert_triage
decilist cost-report decisions.jsonl --decision alert_triage --costs costs.json
```

In Python, pass `name="alert_triage"` to `threshold_table`, `shadow_report` and `cost_report`. Each result lists the names it found in `names`. Without a filter, a report over a log with more than one named decision warns and lists them. A name that is not in the log gives an empty report and a warning.

Use the recommended threshold as that decision's policy:

```python
triage = decisions.define("alert_triage", ..., policy=Policy(min_confidence=0.85))
```

## Shadow mode

Run a named decision in the background next to your current answer. The comparison is logged under the decision's name.

```python
shadow = Shadow(decisions)
triage.observe(shadow, alert, current=analyst_choice)             # background, returns whether it started
decision = await triage.compare(shadow, alert, current=analyst_choice)   # waits, for labelling
```

These pass the definition's policy and name to [shadow mode](shadow-mode.md). `shadow.observe` and `shadow.compare` also accept `name=` directly. A definition with `costs` cannot be used here, because shadow mode compares the model's most probable answer.

## Limits

- The name is a label in your own log. It is not sent to the provider and does not change the model's answer.
- If you change the instructions or options of a decision and keep its name, the log mixes two questions under one name, and `calibrate` warns about it. Give the changed question a new name, such as `alert_triage.v2`.
- Names are unique per `Decilist` or `Client` object. Two objects writing to one log file can use the same name, and their lines are then reported together.
- Remote calls through `Client` log the name on the client side. The runtime does not see it.
