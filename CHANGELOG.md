# Changelog

Notable changes to the Decilist SDK. Decilist is alpha, so the API can still change.

## Unreleased

### Added

- **Named decisions.** `decisions.define(name, instructions, ...)` defines a question once with its options, policy, check and costs. Call the result with only the state. The name is written to the decision log. Python only. See [named decisions](docs/named-decisions.md).
- **Reports per decision.** `decilist calibrate` and `decilist shadow-report` accept `--decision NAME`, and `threshold_table` and `shadow_report` accept `name=`. Reports list the names found in the log and warn when a log mixes several.
- **Cost-aware decisions.** `Costs` states what each mistake costs and what asking a person costs. A decision defined with `costs` picks the answer with the lowest expected cost and abstains when a person is cheaper. Works with `verify`, which then checks answers cheapest first. See [cost-aware decisions](docs/cost-aware-decisions.md).
- **Cost-aware decisions in TypeScript.** `chooseCosted`, `predictCosted`, `applyCosts` and `expectedCosts`, and a `costs` option on `chooseVerified`. The rule runs on the client and gives the same answers as Python.
- **Shadow mode with costs.** A decision defined with costs compares the cost rule's pick with the current answer, and `shadow-report` uses that pick for coverage, agreement and accuracy.
- **Cost replay.** `decilist cost-report` and `cost_report` replay labelled decisions from a log under a cost rule and a confidence rule and compare what each would have cost.
- `apply_costs(decision, costs)` applies a cost table to a decision you already have.
- `Shadow.observe` and `Shadow.compare` accept `name=` and `costs=`, and a named decision has `observe` and `compare` helpers.
- [`docs/capabilities.md`](docs/capabilities.md) lists every capability and where it is available.
- Examples: [`examples/named_decision.py`](examples/named_decision.py) and [`examples/cost_aware.py`](examples/cost_aware.py).

### Changed

- `VerifiedDecision` has a new `costed` field. It is `None` unless the decision was defined with costs.
- `Decilist.decide` and `Client.decide` accept an optional `name=` keyword, which is written to the log.
- A decision log can contain `cost` lines, a `name` field on decision lines, and `status` and `value` on shadow lines written under a cost rule. Lines from direct calls are unchanged.
- The TypeScript `VerifiedDecision` has an optional `costed` field.
- The automatic outcome that `verify` writes for its first check is now written only when that check was on the model's most probable answer. This is always the case without costs.
