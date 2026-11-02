# Shadow mode

Shadow mode lets an async Python app keep using its current answer while Decilist answers the same question in the background. The model's answer is logged alongside the current answer; the background call never returns it to the app. Shadow mode is Python only and requires a running event loop.

```python
from decilist import Decilist, DecisionLog, FunctionProvider, Shadow


def model(question):
    return {"approve": 0.8, "review": 0.2}


log = DecisionLog("decisions.jsonl")
decisions = Decilist([FunctionProvider(model)], log=log)
shadow = Shadow(decisions, max_pending=32)


async def handle(order):
    current = "review"  # The application's existing decision.
    shadow.choose(order, "Choose the next step", {"approve": "Approve", "review": "Review"}, current=current)
    return current


async def shutdown():
    remaining = await shadow.drain(timeout=5)
    print("Still running:", remaining)
```

`choose`, `predict`, `score`, and `observe` return whether work started. Calls are dropped when the pending limit is reached or there is no running event loop; `shadow.dropped` counts these. `shadow.errors` counts failed observations and failed provider attempts, including timeouts. A failed attempt still counts if a later provider recovers. The `compare_choose`, `compare_predict`, `compare_score`, and `compare` methods wait and return the decision or `None` on failure. Use them when inspecting a comparison directly, not to replace the current application answer. `drain` waits for tasks already running without cancelling tasks that exceed the timeout.

`current` accepts `str`, `bool`, `int`, a finite `float`, `None`, or an `Enum` member. Anything else, including NumPy scalars, is counted in `shadow.errors` and is not run; convert it first.

The model still runs and uses CPU, memory, and whatever else its provider needs. Background work is not free. A plain-function provider uses a thread. The usual `local_only` policy still applies; nothing leaves the machine unless the app configured a provider that does that.

## Record outcomes

The background forms return only a `bool`, so they do not provide the `request_id` that `record_outcome` needs. To label observations, run `compare_*` in a task owned by your app:

```python
async def compare_and_label(order, current, expected):
    decision = await shadow.compare_choose(order, "Choose the next step", {"approve": "Approve", "review": "Review"}, current=current)
    if decision is not None:
        log.record_outcome(decision.request_id, expected=expected)
```

The returned decision is for labelling or inspection; the app keeps using its current answer.

## Read the report

```bash
decilist shadow-report decisions.jsonl
decilist shadow-report decisions.jsonl --model my-model --max-disagreements 20 --json
```

The report joins shadow lines to their decision lines by request ID. Coverage is the fraction of observations the model accepted. Agreement counts only observations where the answers can be compared. A score agrees when it is within 0.5 of the current numeric answer; a boolean is not a numeric score. Abstentions and missing or incompatible current answers are not compared. The report gives a Wilson lower bound for agreement, latency percentiles, and recent disagreements. The JSON output includes counts by question kind.

Agreement with the current method is not accuracy: a disagreement may mean the app is wrong or the model is wrong. If you add outcomes with `DecisionLog.record_outcome`, the report includes model accuracy for accepted labelled decisions and current-answer accuracy where an expected answer is available. An outcome that only supplies `correct` labels the model decision, not the current answer. Outcomes may be unrepresentative; review the sample before drawing conclusions.
