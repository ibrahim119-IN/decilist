# Inclusive interval helper

This is a tiny, dependency-free teaching project for [Decilist proof-first review](../../docs/review-proof.md). Its implementation deliberately leaves a documented case uncovered by the existing tests. It is an authored integration example, not an accuracy or speed benchmark.

## Public contract

`within(value, min, max)` is a named export from `within.mjs`. For finite JavaScript numbers with `min <= max`, it returns a boolean indicating whether `value` lies in the **inclusive** interval from `min` to `max`. Both endpoints belong to the interval, including when the endpoints are equal. Values outside the interval return `false`.

The supported inputs are finite numbers only. Behavior for strings, booleans, null, undefined, objects, arrays, NaN, Infinity, or reversed endpoints is unspecified. There is no promise to coerce or validate those inputs, throw an error, or return a particular value for them.

```js
import { within } from './within.mjs';

within(3, 1, 5); // true
within(9, 1, 5); // false
```

## Existing checks

From this directory, run:

```sh
node --test --test-reporter=tap test/within.test.mjs
```

These existing tests cover interior, outside, and upper-endpoint values. They are intentionally incomplete. A passing suite is not a statement that every case in the public contract works.

To investigate from the Decilist repository root, with the current Python package installed and the compatible local model already running:

```sh
python -m decilist review investigate examples/review_proof --source within.mjs --entry within.mjs --regression test/within.test.mjs --contract README.md --task "Check within(value, min, max) against its documented inclusive numeric interval." --model-url http://127.0.0.1:8794/v1/chat/completions --model qwen36-27b-q8 --no-thinking --allow-execution
```

Replace the URL/model alias for your existing server. These values use the optional local Qwen3.6 setup; `--no-thinking` enables its current llama.cpp structured-output compatibility. Investigation executes this trusted project and generated probes in disposable local copies, **not an OS sandbox**. No model is downloaded or started by this command.

Inspect `.decilist-review/investigate-*/report.json`, retained reproduction/workspace files, and any accepted `candidate.patch`. The original source and tests stay unchanged. A model may return no hypothesis or fail to produce an accepted candidate; no model outcome is promised or recorded here. Review any assertion's expectation against the public contract before treating it as a defect.
