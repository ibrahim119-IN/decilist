# decilist

**Build decisions you can test, compare, and run on your own machine.**

Decilist is an early, open-source Decision Intelligence SDK. Ask a bounded question directly in your code, validate its answer, apply a policy, and evaluate the result through the same contract across providers. It is local-first and model-agnostic by design. No decision registration or Playground session is required; reusable decision definitions are an optional future convenience.

**Status: integration alpha, not a completed platform.** Direct SDK calls, an explicit runtime launcher, packaged UI, and a frozen text evaluation suite are implemented. The default demo uses deterministic local rules and an uninformative baseline. Liquid d1-3B compatibility and learned quality are separate acceptance gates; see [verification](docs/verification.md). OpenAI Decisions is planned. Packages are not published to npm or PyPI.

![Local decision playground](reports/ui/playground-desktop.png)

## Why this exists

Calling a model is one step. A developer still needs to decide what counts as a valid answer, when to abstain, which fallback may receive the input, and whether a new provider actually improves their workload.

Decilist's intended value is that complete development loop:

- `choose`, `predict`, and `score` share a versioned contract across Python, TypeScript, and HTTP.
- Runtime checks reject unknown categories, invalid distributions, and inconsistent answers.
- A local-only policy blocks remote providers before inference. Low confidence, invalid output, errors, and timeouts are visible in the attempt trace.
- Evaluation keeps accuracy, coverage, abstention, and latency separate. Unknown cost stays `null`.
- The same domain-neutral example runs in code and a local Playground.

This does not introduce a new trained model or claim a speed advantage over vendor APIs. Differentiation must be demonstrated by lower integration effort and better measured application decisions. See [architecture](docs/architecture.md) and [evaluation methodology](docs/evaluation.md).

## Start locally

Prerequisites: Python 3.12 recommended (package minimum 3.11), Node.js 22+, Git. Run commands from the repository root.

```bash
python -m venv .venv
```

Activate `.venv` (`.venv\Scripts\Activate.ps1` on Windows PowerShell, `source .venv/bin/activate` on macOS/Linux), then:

```bash
python -m pip install --upgrade pip
python -m pip install -c requirements-dev.lock -e "packages/python[server,dev]"
npm ci
python -m decilist runtime start --provider demo
```

Open **http://127.0.0.1:8765/demo** for a normal request-composer example, or **http://127.0.0.1:8765/** for the optional Playground. The app asks one focused clarification in place, preserves its known context, and prepares a local request preview. It does not generate the requested summary or execute external actions. This loopback development server has no production authentication, API key, external fonts, analytics, or cloud inference. Dependency installation requires internet access; the default demo does not.

To run a program with its runtime in one command, after `npm run build`:

```bash
python -m decilist runtime run --provider demo -- node examples/workflow.mjs
```

## Python SDK

Connect once and use direct calls. This example uses the explicitly selected rules runtime:

```python
import asyncio
from decilist import Client

async def main():
    async with await Client.connect() as decisions:
        result = await decisions.predict('{"complete":true,"valid":true}', "Is the workflow ready to proceed?")
        print(result.value if result.status == "accepted" else "Needs review")

asyncio.run(main())
```

Python can also run in process with `Decilist([provider])`. Both paths expose `choose(state, instructions, options)`, `predict(state, instructions)`, and `score(state, instructions, levels)`. Prediction returns a boolean with P(true) in `probabilities["true"]`; scoring returns a probability-weighted index of 2–10 ordered levels, starting at zero. See [runtime setup and direct integration](docs/runtime.md).

## TypeScript SDK

Build the workspace with `npm run build`. The package is currently consumed from the workspace, not a public registry.

```ts
import { Decilist } from "@decilist/sdk";

const sdk = await Decilist.connect(); // verifies local runtime readiness once
const result = await sdk.choose(
  JSON.stringify({ complete: true, valid: true }),
  "Choose the next workflow action.",
  { proceed: "Ready", retry: "Transient error", review: "Needs a person" },
);
if (result.status === "accepted") {
  // value is typed as "proceed" | "retry" | "review"
  console.log(result.value);
}
```

The TypeScript SDK performs request and response validation. The Python runtime owns model loading, provider policies, and evaluation. The explicit launcher starts or reuses the correct runtime and stops only the runtime it owns. Native TypeScript model execution is not implemented; imports do not start a process or download anything.

## HTTP for other languages

`POST /v1/decisions`, with `Content-Type: application/json`:

```json
{
  "question": {
    "kind": "choose",
    "state": "{\"complete\":true,\"valid\":true}",
    "instructions": "Choose the next workflow action.",
    "options": {"proceed":"Ready","retry":"Transient error","review":"Needs a person"}
  },
  "policy": {"local_only":true,"min_confidence":0.7,"timeout_ms":5000}
}
```

The demo's rules only implement the documented workflow gate; they do not interpret arbitrary prompts. Generated OpenAPI is served at `/openapi.json`, with a checked-in copy in [contracts/openapi.json](contracts/openapi.json).

## Verify and compare

```bash
python -m pytest -q
npm test
python -m benchmarks.run --output reports/local/workflow.json
```

The original 12-case smoke suite checks wiring. Its perfect rule score follows from rules implementing its labels; it is **not** model-quality evidence. [Initial measured report](reports/baseline-2026-10-25.json).

The [new text suite](docs/evaluation-v2.md) has 20 development and 40 held-out cases across all three operations, with frozen labels and policy. It reports classification accuracy, numeric score error, coverage, abstention, validity, latency, and failure cases. The original rules do not implement this different task, so no misleading numeric comparison is made. The [uniform reference report](benchmarks/results/request-text-uniform.json) is a baseline, not a model result.

## Repository map

| Path | Purpose |
| --- | --- |
| `packages/python` | Runtime, typed contracts, providers, HTTP client, evaluation |
| `packages/typescript` | Typed SDK, validation, HTTP transport |
| `apps/api` | Thin repository entry point to the installed server |
| `packages/python/src/decilist/static` | Optional Playground and normal-app example |
| `contracts` | Shared parity cases and OpenAPI |
| `examples` | Domain-neutral workflow gate |
| `benchmarks` | Original smoke suite and frozen text evaluation |
| `docs` | MVP plan, architecture, provider compatibility, licensing |

## License and roadmap

Decilist's own code is **Apache-2.0**. Model weights and vendor code are not included and retain their own terms. Liquid d1-3B is open-weight under the LFM Open License, not automatically covered by our code license. See [third-party terms](THIRD_PARTY.md) and the [Liquid setup gate](docs/providers.md).

The first presentation target is **Sunday, November 1, 2026**, Cairo time. The [implementation plan](docs/mvp-plan.md) prioritizes real local inference, direct SDK integration, simpler runtime setup, one optional Playground workspace, and a normal-app demonstration. See also the [demo walkthrough](docs/demo.md) and [contributing](CONTRIBUTING.md).
