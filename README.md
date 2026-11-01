<p align="center">
  <img src="docs/assets/hero.svg" alt="Decilist: put a typed decision inside your app. Open-source SDK for Python, TypeScript and HTTP. You bring the model." width="100%">
</p>

<p align="center">
  <a href="https://github.com/ibrahim119-IN/decilist/actions/workflows/ci.yml"><img src="https://github.com/ibrahim119-IN/decilist/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-b9f48c?labelColor=142023" alt="License: Apache-2.0"></a>
  <img src="https://img.shields.io/badge/status-alpha-e8b85c?labelColor=142023" alt="Status: alpha">
  <img src="https://img.shields.io/badge/python-3.11%2B-9cafb2?labelColor=142023" alt="Python 3.11 or newer">
  <img src="https://img.shields.io/badge/node-22%2B-9cafb2?labelColor=142023" alt="Node 22 or newer">
</p>

Decilist lets your app ask a small question, get back one of the answers you allowed, and know clearly when there is no answer. It works the same way from Python, TypeScript and plain HTTP, and it runs on your own machine.

> [!IMPORTANT]
> **Decilist is an SDK, not a model.** Installing it gives you the contract, the policies and the checks. It includes fixed demo rules so you can test your wiring, but those rules aren't AI. For real decisions, you still need to download a model or bring your own.

## What's in the box

![Your app asks the Decilist SDK a question. The SDK asks a provider and checks the answer. Demo rules are included. Your own model is wrapped. LiquidAI/d1-3B is a separate download.](docs/assets/sdk-and-model.svg)

| Provider | What it is | What you need |
|---|---|---|
| Demo rules | Fixed rules for one example workflow. Use them to test your wiring. | Nothing more |
| Your function or model | Any local code that returns probabilities | Your code, wrapped with `FunctionProvider` |
| [`LiquidAI/d1-3B`](https://huggingface.co/LiquidAI/d1-3B) | A decision model that runs on your machine | A separate 6.25 GB download. Tested on an RTX 3090 |
| OpenAI Decisions | Cloud provider | Planned, not built |

## Try it in one minute

You need Python 3.11 or newer and Git. You don't need a GPU or a model.

```bash
python -m venv .venv
```

Activate the environment. On Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

On macOS or Linux:

```bash
source .venv/bin/activate
```

Then install from GitHub. Decilist isn't on PyPI or npm yet.

```bash
python -m pip install "decilist[server] @ git+https://github.com/ibrahim119-IN/decilist.git#subdirectory=packages/python"
```

Save this as `app.py`:

```python
import asyncio
import json

from decilist import Decilist, FunctionProvider


def my_rules(question):
    order = json.loads(question.state)
    winner = "refund" if order["days_since_purchase"] <= 30 else "escalate"
    return {"refund": float(winner == "refund"), "escalate": float(winner == "escalate")}


decisions = Decilist([FunctionProvider(my_rules, name="my-rules")])


async def main():
    answer = await decisions.choose(
        '{"days_since_purchase": 12}',
        "Pick the next step for this support request.",
        {"refund": "Refund the customer", "escalate": "Send to a person"},
    )
    if answer.status == "accepted":
        print("Do this:", answer.value)
    else:
        print("No answer. Ask a person.")


asyncio.run(main())
```

Run it with `python app.py`. It prints `Do this: refund`. The answer here comes from your own rules, so swapping in a model doesn't change the rest of your code.

## How a decision runs

There are three kinds of question:

| Call | Use it to | You get back |
|---|---|---|
| `choose(state, instructions, options)` | Pick one of up to 64 named options | The option's name |
| `predict(state, instructions)` | Answer yes or no | `True` or `False` |
| `score(state, instructions, levels)` | Rate on 2 to 10 ordered levels | A number, the expected level |

Each call goes through the same steps. You list your providers in order, and Decilist tries them until one gives an answer that passes.

```mermaid
flowchart TD
    A["Your code calls choose, predict or score"] --> B{"Another provider<br/>in your list?"}
    B -- "No" --> X["status: abstained<br/>value: null"]
    B -- "Yes" --> C{"Allowed by<br/>local_only?"}
    C -- "No" --> L["Record: blocked"] --> B
    C -- "Yes" --> D["Ask the provider,<br/>up to timeout_ms"]
    D -- "Timeout or error" --> M["Record: timeout or error"] --> B
    D -- "Answer" --> E{"Valid probabilities<br/>for your labels?"}
    E -- "No" --> N["Record: invalid_output"] --> B
    E -- "Yes" --> F{"Confidence at least<br/>min_confidence?"}
    F -- "No" --> O["Record: low_confidence"] --> B
    F -- "Yes" --> G["status: accepted<br/>value, confidence, provider, model"]
    classDef ok fill:#b9f48c,stroke:#5c8f35,color:#0d1416
    classDef stop fill:#f5e2b8,stroke:#e8b85c,color:#0d1416
    class G ok
    class X stop
```

The defaults are `local_only=True`, `min_confidence=0.7` and `timeout_ms=5000`. Every result lists its attempts, so you can see what was tried and why. Decilist never moves your data to a cloud provider as a hidden fallback. With `local_only` on, a remote provider is blocked before it sees anything.

Confidence is the model's own estimate. It isn't a guarantee that the answer is right.

## Connect it to your project

![In Python, call Decilist in the same process. From TypeScript or any other language, send HTTP to a local Decilist runtime.](docs/assets/two-ways-to-connect.svg)

Find the place in your code where a person or a long `if` chain makes a small decision today. Pass the facts as `state`, list the allowed answers, then branch on the result. Your app keeps its own actions, screens and permissions.

<details open>
<summary><b>Python, in the same process</b></summary>

This is the `app.py` above. To connect your own model instead of rules, return its probabilities. The labels must match your options exactly and sum to 1.

```python
def my_model_provider(question):
    features = json.loads(question.state)
    return my_model.predict_proba(features)  # {"refund": 0.9, "escalate": 0.1}


decisions = Decilist([FunctionProvider(my_model_provider, name="my-model", model="my-model-v1")])
```

[`examples/own_model.py`](examples/own_model.py) is a complete version you can run, and it shows the abstained path too. `FunctionProvider` runs on the event loop, so a slow model needs its own provider class with `async def infer` that runs the model in a thread. The [architecture notes](docs/architecture.md) describe that interface.

</details>

<details>
<summary><b>Python, through the local runtime</b></summary>

Start the runtime in one terminal and keep it open:

```bash
python -m decilist runtime start --provider demo
```

Then call it from another terminal, with the same environment active:

```python
import asyncio

from decilist import Client


async def main():
    async with await Client.connect() as decisions:
        answer = await decisions.predict('{"complete":true,"valid":true}', "Is the workflow ready to proceed?")
        print(answer.value if answer.status == "accepted" else "Needs review")


asyncio.run(main())
```

The demo rules only understand the example workflow. Other questions abstain until you start a real model.

</details>

<details>
<summary><b>TypeScript</b></summary>

You need Node.js 22 or newer. The TypeScript SDK isn't on npm yet, so build a package from a clone:

```bash
git clone https://github.com/ibrahim119-IN/decilist.git
```

```bash
cd decilist
```

```bash
npm ci
```

```bash
npm run build
```

```bash
npm pack ./packages/typescript
```

That creates `decilist-sdk-0.1.0-alpha.1.tgz`. Install it in your project with `npm install <path to the .tgz>`, then:

```javascript
import { Decilist } from "@decilist/sdk";

const decisions = await Decilist.connect();
const answer = await decisions.predict(
  '{"complete":true,"valid":true}',
  "Is the workflow ready to proceed?",
);
console.log(answer.status === "accepted" ? answer.value : "Needs review");
```

TypeScript talks to the Python runtime and doesn't run models itself. Start your app and a runtime together with one command:

```bash
python -m decilist runtime run --provider demo -- node app.mjs
```

</details>

<details>
<summary><b>Any other language, over HTTP</b></summary>

With the runtime running, send a `POST` to `http://127.0.0.1:8765/v1/decisions`. This uses macOS, Linux or Git Bash quoting:

```bash
curl -s http://127.0.0.1:8765/v1/decisions -H "Content-Type: application/json" -d '{"question":{"kind":"choose","state":"{\"complete\":true,\"valid\":true}","instructions":"Choose the next workflow action.","options":{"proceed":"Ready","retry":"Transient error","review":"Needs a person"}},"policy":{"local_only":true,"min_confidence":0.7,"timeout_ms":5000}}'
```

The answer includes `status`, `value`, `probabilities`, `confidence`, `provider`, `model` and `attempts`. The full schema is in [`contracts/`](contracts/).

</details>

## Add a real model

```mermaid
flowchart LR
    A["Install the SDK"] --> B["Test your wiring<br/>with rules"]
    B --> C{"Do you have<br/>a model?"}
    C -- "Yes" --> D["Wrap it with<br/>FunctionProvider"]
    C -- "No" --> E["Download<br/>LiquidAI/d1-3B"]
    D --> F["Test it on your<br/>own cases"]
    E --> F
    F --> G["Decide what your app<br/>does when it abstains"]
    classDef ok fill:#b9f48c,stroke:#5c8f35,color:#0d1416
    class G ok
```

To use `LiquidAI/d1-3B`, first read its [license](https://huggingface.co/LiquidAI/d1-3B/blob/main/LICENSE) and the vendor code you'll be running. Use a separate environment, and install PyTorch for your machine from the [official selector](https://pytorch.org/get-started/locally/). Then:

```bash
python -m pip install "decilist[server,liquid] @ git+https://github.com/ibrahim119-IN/decilist.git#subdirectory=packages/python"
```

This downloads the weights once. Nothing downloads unless you pass `--allow-download`:

```bash
python -m decilist runtime setup liquid --allow-download --allow-model-code --device cuda
```

This starts the runtime with the cached model and doesn't download anything:

```bash
python -m decilist runtime start --provider liquid --allow-model-code --device cuda
```

Your calls stay the same. For a slower machine, raise the timeout once with `Client.connect(policy=Policy(timeout_ms=30000))`. If the model fails to load, you get an error, and Decilist doesn't switch to something else behind your back. See the [provider](docs/providers.md) and [runtime](docs/runtime.md) guides for details.

![The Decilist SDK with server dependencies is 0.02 GB. The Python packages for d1-3B are 4.60 GB. The d1-3B weights are 6.25 GB.](docs/assets/chart-download-size.svg)

### What we measured with d1-3B

![Of 81 held-out calls, 36 were accepted and correct, 18 accepted and wrong, and 27 abstained. 66.7% correct among accepted answers.](docs/assets/chart-held-out.svg)

| Measure | Result |
|---|---|
| Speed, warm, on our text task | 45.63 ms median, 47.76 ms at p95 |
| Peak GPU memory | 6.36 GB |
| Machine | Windows 11, NVIDIA RTX 3090 |
| Coverage across all held-out calls | 60% (72 of 120) |
| CPU, Apple GPU, Linux GPU | Not measured by us. `--device cpu` exists, and speed depends on the machine |

Full method and failures are in the [model validation report](docs/model-validation.md). These numbers cover one small English task, so please don't read them as how the model will do on yours.

## See it in your browser

With the runtime running, open:

- **<http://127.0.0.1:8765/>** for the Playground, where you can try questions and copy the code.
- **<http://127.0.0.1:8765/demo>** for a small ordinary app that uses Decilist behind the scenes. It only prepares a local preview and never sends anything.

![The Decilist Playground showing an accepted decision, its probabilities and SDK code.](docs/assets/playground.png)

## Status and limits

Decilist is **alpha**, so the API can change.

| Area | State |
|---|---|
| **Works today** | Python and TypeScript SDKs, HTTP API, policies, attempt traces, the local runtime, the Playground and the examples |
| **Checked by CI** | Tests and the example apps, on Windows and Ubuntu, with the demo rules. CI doesn't run a model |
| **Checked by hand** | d1-3B through both SDKs, on Windows with an RTX 3090 |
| **Not checked** | macOS, GPU on Linux, first use by outside developers |
| **Not measured** | Developer time saved. We don't have a number for this |
| **Not published** | PyPI and npm packages |

The repository also has experimental helpers for failing tests and code review. They aren't part of the core SDK, and several of their experiments recorded failures. The [docs index](docs/README.md) links to those results.

## What's where

| Folder | What's inside |
|---|---|
| [`packages/python`](packages/python) | Python SDK, local runtime, Playground |
| [`packages/typescript`](packages/typescript) | TypeScript SDK |
| [`contracts`](contracts) | The shared decision contract |
| [`examples`](examples) | Small programs you can run |
| [`docs`](docs/README.md) | Guides, plus the research record |
| [`tests`](tests) | Test suite |
| [`benchmarks`](benchmarks), [`reports`](reports/README.md) | Evaluation tools and recorded results |

## Run the tests

From a clone, with an environment active and Node.js 22 installed:

```bash
python -m pip install -c requirements-dev.lock -e "./packages/python[server,dev]"
```

```bash
npm ci
```

```bash
python scripts/restore_triage_v5_fixture_evidence.py
```

```bash
python -m pytest -q
```

```bash
npm test
```

The restore step writes its fixtures to `../../work/triage-v5-lab`, outside the clone. To put them somewhere else, set `DECILIST_TRIAGE_V5_WORK_ROOT` to the same folder before the restore and test commands. The default tests don't download a model or call a paid API. See [CONTRIBUTING.md](CONTRIBUTING.md) to contribute and [SECURITY.md](SECURITY.md) to report a security issue.

## License

Decilist is licensed under [Apache-2.0](LICENSE). Models and their weights have their own terms and aren't included. See [THIRD_PARTY.md](THIRD_PARTY.md).
