# Local runtime and direct SDK calls

Connect once, then call `choose`, `predict`, or `score` directly. Saved decisions, the Playground, accounts, and dashboards are optional. Python can also use `Decilist([provider])` in process; TypeScript sends HTTP to the Python runtime.

## Install and run

Use Python 3.11 or later; TypeScript also needs Node.js 22 or later. The local launcher is verified on Windows on the presentation machine. Other operating systems have not been verified. These alpha packages are local artifacts, not published registry packages.

From this repository, install into a local environment:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install "./packages/python[server]"
npm install
npm run build
```

To install a built wheel in a fresh project instead, use `python -m pip install "./decilist-0.1.0a1-py3-none-any.whl[server]"`. Install the TypeScript tarball with `npm install ./decilist-sdk-0.1.0-alpha.1.tgz`.

One command runs an application with a ready local runtime and closes the runtime afterward:

```powershell
.venv\Scripts\python -m decilist runtime run --provider demo -- node app.mjs
```

`demo` is a deterministic rules baseline for wiring checks. It is not a learned model or an estimate of model quality. Choose a provider explicitly; failures never switch providers silently.

The npm package also provides a thin launcher for that same installed Python runtime:

```powershell
npx decilist --python .venv\Scripts\python.exe runtime run --provider demo -- node app.mjs
```

The launcher keeps the HTTP server in its Python process, waits for compatible health, runs the application without a shell, and returns the application's exit code. It passes the endpoint in `DECILIST_URL`, which both HTTP SDKs use by default. If a matching runtime already owns the port, it reuses it and leaves it running. An unrelated service, incompatible contract, or different provider produces an error. No runtime is started by importing an SDK.

For a long running shared runtime or the optional Playground, use:

```powershell
.venv\Scripts\python -m decilist runtime start --provider demo
.venv\Scripts\python -m decilist runtime status
```

`start` stays in the foreground. Open the printed loopback URL for the optional Playground; Ctrl+C stops the runtime started by that command. `status` can run separately and exits unsuccessfully for unavailable or incompatible services. `--port 8766` selects another loopback port; `status --url http://127.0.0.1:8766` inspects it. Remote hosting and authentication are outside this launcher.

## TypeScript

Create `app.mjs` in the project containing the installed SDK:

```javascript
import { Decilist } from "@decilist/sdk";
const decisions = await Decilist.connect();
const answer = await decisions.predict(
  '{"complete":true,"valid":true}', "Is the workflow ready to proceed?"
);
console.log(answer.status === "accepted" ? answer.value : "Needs review");
```

Use `Decilist.connect({baseUrl: "http://127.0.0.1:8765"})` for a configured endpoint. Connection checks service identity, readiness, runtime version metadata, and decision schema version before sending decision evidence. `waitMs` defaults to 10 seconds and is configurable. Existing `new Decilist(transport, policy)` calls remain supported; the HTTP transport checks health before its first decision. Custom transports remain available.

## Python

```python
import asyncio
from decilist import Client

async def main():
    async with await Client.connect() as decisions:
        answer = await decisions.predict('{"complete":true,"valid":true}', "Is the workflow ready to proceed?")
        print(answer.value if answer.status == "accepted" else "Needs review")
asyncio.run(main())
```

`Client.connect(base_url, wait_seconds=10)` verifies readiness. Existing `Client(base_url)` checks health on its first decision. A shared policy can be configured once using `Client.connect(policy=Policy(timeout_ms=30000))`; import `Policy` from `decilist`. Closing a client closes its connections; it does not stop the runtime. In process Python uses the same direct methods on an engine configured with one or more providers.

## Explicit Liquid setup

Install the optional model dependencies into the model environment, separately from the lightweight SDK environment if needed. Review the model terms and pinned vendor Python code at [LiquidAI/d1-3B](https://huggingface.co/LiquidAI/d1-3B). Setup makes an explicit network request, reports repository asset size when available, and uses Hugging Face's resumable cache and transfer progress:

```powershell
python -m pip install "./packages/python[server,liquid]"
python -m decilist runtime setup liquid --allow-download --allow-model-code --device cuda
python -m decilist runtime run --provider liquid --allow-model-code --device cuda -- node app.mjs
```

Setup pins the model revision declared by `LiquidLocalProvider`. Startup loads cached assets only. Both commands require explicit permission to run the model's vendor code; startup does not download missing assets. `--device cuda:1` selects a second CUDA GPU; `--device cpu` is an explicit alternative, with performance dependent on the machine. No paid or remote inference provider is configured by these commands.

Model load failure reports an actionable error and leaves no fallback server. The model's dependency/device compatibility and actual decision quality must be measured separately; successful packaging checks do not establish either. A threshold is a policy, not a guarantee of correctness. Runtime or transport failures raise errors; uncertain or unsuccessful provider attempts return `status="abstained"` with no decision value.

The shared request contract defaults to a five second provider timeout. For slower local inference, configure it once in the SDK: TypeScript `Decilist.connect({policy: {timeout_ms: 30000}})` or Python `Client.connect(policy=Policy(timeout_ms=30000))`. Loading the model happens before the runtime becomes ready; inference timeouts apply after a decision request arrives.

The launcher provides graceful HTTP shutdown. Native model work already executing may need to finish before Python exits; hard GPU cancellation and detaching background services are outside this scope. Use direct executable commands after `--` (for example `node app.mjs`) instead of shell operators. On Windows, normal console Ctrl+C is supported; externally terminating the npm launcher process is not a managed shutdown mechanism.
