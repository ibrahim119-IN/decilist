# Verified decisions

`choose_verified` (or `chooseVerified` in TypeScript) asks for a choice, then calls your application's check on the ranked labels. The model sets the order; your check decides whether a label passes. A truthy return passes and is retained as the result. A falsy return fails. `verified` means only that your check passed, so the guarantee is only as good as that check.

The result has one of three statuses:

- `verified`: a check passed; `value` is its label and `result` is the returned object.
- `fallback`: no check passed or the model abstained, and the app's fallback was called. `value` is null and `result` is whatever the fallback returned, even if falsy.
- `unresolved`: no check passed or the model abstained, and no fallback was supplied. Both `value` and `result` are null.

The original `decision` and the checks in `tried` are retained. An accepted decision is checked in descending probability, including zero-probability options, up to `max_tries` (TypeScript: `maxTries`, default 3). Equal probabilities retain their order in the decision, with the decision's selected value first. An abstention skips the checks. If a check raises an exception, it fails and the next option is tried; only the exception type name is recorded, not its message. A fallback exception propagates to the caller.

The default policy abstains below confidence 0.7. Rankings across many options often have a low top probability, so use `policy=Policy(min_confidence=0.0)` in Python (or `policy: {min_confidence: 0.0}` in TypeScript) when you want the ranking despite uncertainty and rely on your check as the safeguard. The policy's timeout applies to the model only. There is no timeout on the check or fallback; arrange one in your own application if needed. Python runs plain functions in a worker thread and awaits async functions and awaitable returns.

A [named decision](named-decisions.md) can hold `verify`, `fallback` and `max_tries`, so every call runs the check. With [costs](cost-aware-decisions.md), the check runs on the answers in order of expected cost, cheapest first, and skips any answer that costs more than asking a person; the cost result is in `VerifiedDecision.costed`. The automatic outcome for the first check is written only when that check was on the model's most probable answer.

Choices are limited to 64 options. For a larger pool, rank it first using your own cheap estimate, then pass the best 64 or fewer to Decilist. See the [offline example](../examples/verified_choice.py).
