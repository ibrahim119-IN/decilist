# Explicit local model profiles

`scripts/setup_stronger_local.py` supports explicit model profiles. The integrated plans and setup guards passed 21 focused tests using stubs and pure plans. Separately, on 2026-10-26 the public setup helper successfully loaded Qwen3.6-27B Q8_0 on the existing Windows machine with two RTX 3090 24 GB cards, 16,384 context tokens, a 3:2 layer split and native `--reasoning off`. It verified the full pinned weight SHA-256, owned listener PID 64136, serving context, actual CUDA DLL paths/hashes and the default closed-thinking template prefix. This is observed setup readiness on that hardware, not a general compatibility, diagnostic-quality or human-speed claim.

The retained local manifest is `L:/CodexData/Decilist/work/triage-v6-model/launches/20261026T201514864850Z-qwen36-27b-q8/server-ready-manifest.json`. That launch reused the pinned runtime and existing CUDA DLLs through the explicit directory options below. The setup helper made zero generation requests; the commands in this guide describe checkout usage.

The existing default remains Qwen3-Coder-30B-A3B-Instruct Q4_K_M. Select Qwen3.6 explicitly; installing or importing the SDK does not select, download or start either model. Windows x64 CUDA is the setup helper's supported target. The ordinary `decilist triage` command and existing provider/config schemas are unchanged.

| Setting | Existing default | Explicit stronger candidate |
| --- | --- | --- |
| `--profile` | `qwen3-coder-30b-a3b-q4km` | `qwen36-27b-q8` |
| GGUF | Qwen3-Coder-30B-A3B-Instruct Q4_K_M | Qwen3.6-27B Q8_0 |
| Context / slots | 8,192 / 1 | 16,384 / 1 |
| Loopback port | 8792 | 8794 |
| Physical GPUs | 0 | 0,1, layer split 3:2 |
| Batch / ubatch | 512 / 128 | 256 / 64 |
| Thinking setting | Existing model default | Native `--reasoning off` |
| Config file | `decilist-model.json` | `decilist-model-qwen36-27b-q8.json` |

## Keep setup storage on L:

Run from the checkout. Use a dedicated cache directory and Python environment. SDK installation needs no GPU framework. The separate Hub packages are needed only for an explicit model download; the helper never installs packages itself. These are checkout commands, not a claim that this candidate is published on PyPI.

```powershell
$setupRoot = 'L:/CodexData/Decilist/work/qwen36-local'
New-Item -ItemType Directory -Force "$setupRoot/tmp", "$setupRoot/pip-cache" | Out-Null
$env:TEMP = "$setupRoot/tmp"
$env:TMP = "$setupRoot/tmp"
$env:TMPDIR = "$setupRoot/tmp"
$env:PIP_CACHE_DIR = "$setupRoot/pip-cache"
$env:PYTHONDONTWRITEBYTECODE = '1'
python -B -m venv "$setupRoot/.venv"
$python = "$setupRoot/.venv/Scripts/python.exe"
& $python -B -m pip install -e ./packages/python
& $python -B -m pip install huggingface-hub==1.33.0 hf-xet==1.7.0
```

The helper sets Hub/Xet, CUDA, generic cache and temporary directories beneath `--cache-dir` before an explicit download or child process. It rejects a pre-imported native Xet module or incompatible cached Hub configuration; run it in a fresh CLI process. Before calling Hub it validates the selected artifact path, the local `.cache/huggingface` directories and all existing entries in that metadata tree. Existing links/junctions that redirect those paths or its managed directories outside that root are rejected. Supplying an existing runtime or CUDA directory is an explicit read-only reuse option; generated logs, downloads, temporary files and caches still belong to the selected root. The offline flag and loopback binding do not establish OS-level network isolation.

## Inspect the plan before using resources

`plan` prints the selected pins, launch arguments and storage locations. It creates no cache, accesses no network, probes no GPU and starts no runtime:

```powershell
& $python -B scripts/setup_stronger_local.py plan --profile qwen36-27b-q8 --cache-dir $setupRoot
```

After a coordinated load window, an explicit download uses the fixed revision and verifies size and SHA-256. Serving verifies the full model again and starts an owned process. Downloads are cached; subsequent serves use the local files. Repeating `download` may perform Hub metadata access, so use `serve` directly for subsequent offline starts.

```powershell
& $python -B scripts/setup_stronger_local.py download --profile qwen36-27b-q8 --cache-dir $setupRoot
& $python -B scripts/setup_stronger_local.py serve --profile qwen36-27b-q8 --cache-dir $setupRoot
```

Do not run this alongside another model unless measured headroom permits it. The new default requires at least 20,480 MiB free on physical GPU 0 and 14,336 MiB on GPU 1, available host RAM equal to the model bytes plus 8 decimal GB, and 3 decimal GB spare disk before loading. These conservative launch budgets are gates, **not evidence that the model fits**. The helper measures before verification and immediately before spawning, resolves the selected devices to measured GPU UUIDs, and records both measurements. Existing single-GPU Coder defaults retain their previous behavior.

You can change GPUs, tensor split, layers, context, batches and host reserve explicitly. Changing the candidate's audited resource layout requires an explicit positive free-memory budget per selected GPU. `--main-gpu` is an index into the selected visible device list. This example only prints a proposed alternate layout:

```powershell
& $python -B scripts/setup_stronger_local.py plan --profile qwen36-27b-q8 --cache-dir $setupRoot `
  --gpus 1,0 --tensor-split 2,3 --main-gpu 1 --gpu-layers 99 `
  --context 16384 --batch-size 256 --ubatch-size 64 `
  --min-free-gpu-mib 14336,20480 --host-reserve-gb 12
```

Use `--runtime-dir <existing-pinned-runtime>` when serving to reuse the exact pinned executable. `--cuda-dll-dir <existing-CUDA-DLL-directory>` reuses the three required CUDA DLLs; on `download` it also skips the separate CUDA archive. The model download command still prepares its own runtime archive. These options do not install Torch or change another environment. On new-profile readiness, the helper checks the actual owned process's loaded DLL paths and hashes against that chosen directory. Review user-supplied DLL origins and terms separately.

The helper refuses an occupied port and never stops or adopts another service. Keep its serving terminal open. Ctrl+C terminates only its newly launched child. New-profile launch logs, retained failures and readiness metadata are stored under `launches/<UTC>-qwen36-27b-q8/` inside the cache. A failed start does not produce a new identity config; an older config may remain and is not a readiness signal by itself.

## Configure V6 after serving

The launch helper's two-field identity config is a lower-level config; it does not supply the five-field identity discovered for V6. Once the server is ready, run the existing metadata/template setup command against the explicit candidate endpoint and choose a **new** output file:

```powershell
& $python -B -m decilist.triage_setup --profile qwen3_6_closed_thinking `
  --url http://127.0.0.1:8794 --output "$setupRoot/NEW-V6.json"
```

This verifies the native profile and records the model, build, generation profile, chat-template hash and model path. It requests no generation and refuses to overwrite an existing file. Then pass that V6 config and the same endpoint to the workflow. Replace the illustrative project path, existing test selectors and production directory with your own; choose a new work directory outside the project:

```powershell
decilist debug --url http://127.0.0.1:8794 `
  --model-config "$setupRoot/NEW-V6.json" --project L:/path/to/app `
  --test tests/test_bug.py::test_bug --regression tests/test_existing.py `
  --production-root src --work-dir "$setupRoot/runs/new-v6-run"
```

`decilist debug` selects the V6 line-edit workflow. It performs the requested diagnostic/repair workflow in its disposable copy and checks unchanged tests; this example does not request applying edits to the original project. Setup readiness alone does not validate a particular repair. See the [V6 quickstart](triage-v6-quickstart.md) for the same workflow through Python.

## Optional lower-level V3 and SDK integration

After readiness, the candidate writes an identity-only config at `$setupRoot/decilist-model-qwen36-27b-q8.json`, preserving the old default config. Its separate port must be supplied explicitly to consumers:

```powershell
& $python -B -m decilist.triage_v3 --log failed.log --project . `
  --model-config "$setupRoot/decilist-model-qwen36-27b-q8.json" `
  --url http://127.0.0.1:8794 --design diagnostic
```

The source-checkout HTTP example uses `--model-url`, while its own service port remains separate:

```powershell
& $python -B -m examples.llama_http `
  --model-config "$setupRoot/decilist-model-qwen36-27b-q8.json" `
  --model-url http://127.0.0.1:8794 --port 8793
```

That HTTP example requires the existing Python `server` extra. Existing Python and TypeScript HTTP clients then target the wrapper on port 8793 using the unchanged decision contract. Direct Python integration also selects the endpoint explicitly:

```python
import asyncio
from pathlib import Path
from decilist import Decilist, Policy
from decilist.llama_cpp import LlamaCppProvider
from decilist.triage_v3 import load_qwen_config

async def main():
    config = load_qwen_config(Path("L:/CodexData/Decilist/work/qwen36-local/decilist-model-qwen36-27b-q8.json"))
    async with LlamaCppProvider(base_url="http://127.0.0.1:8794", **config) as provider:
        result = await Decilist([provider]).choose(
            "The retry budget is exhausted.", "Choose the next action.",
            {"retry": "Retry if budget remains", "review": "Stop retrying and request review"},
            policy=Policy(local_only=True, min_confidence=0.7),
        )
        print(result.value if result.status == "accepted" else result.status)

asyncio.run(main())
```

No endpoint field is added to the lower-level model config. The optional examples in this section use the helper's two-field config, rather than the V6 config created by `triage_setup`. These integration examples have not been run against Qwen3.6. Structural provider readiness and token probabilities do not prove a diagnosis correct or establish calibrated confidence.

## Pins and non-thinking readiness

The selected official GGUF repository is [`ggml-org/Qwen3.6-27B-GGUF`](https://huggingface.co/ggml-org/Qwen3.6-27B-GGUF/tree/1132d03976d88e30a09ab0ddbf1531d5e1b46e7c), revision `1132d03976d88e30a09ab0ddbf1531d5e1b46e7c`. `Qwen3.6-27B-Q8_0.gguf` is 28,595,762,464 bytes with SHA-256 `73f8260284708ed78ae266df672288b6ad1f2c73ec7ffeb7514b5cecdba646c9`. The audited upstream publisher revision is `Qwen/Qwen3.6-27B@6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`; its [model license](https://huggingface.co/Qwen/Qwen3.6-27B/resolve/6a9e13bd6fc8f0983b9b99948120bc37f49c13e9/LICENSE) is Apache-2.0.

The runtime remains llama.cpp b11889 at commit `d854d98bae598388ecea1ed84396587e5edc326d`, with the existing pinned Windows CUDA archives and executable hash. llama.cpp is MIT licensed; NVIDIA CUDA dependency terms are separate.

The retained source audit identifies native [`--reasoning off`](https://github.com/ggml-org/llama.cpp/blob/d854d98bae598388ecea1ed84396587e5edc326d/common/arg.cpp#L3739) as the control that sets the default thinking parameter false. `--reasoning-format none` alone only controls parsing. The candidate uses both, with f16 K/V cache and one slot. After identity, serving-context, owned-listener and loaded-DLL checks, the helper makes one neutral `/apply-template` request without a request override. It requires the pinned publisher template's closed thinking block before writing readiness. This check passed in the retained launch above, with zero generation requests. It is not a quality result. Actual requests can still change template parameters; the caller must retain and inspect its own settings.

Keep stronger-model use experimental. The retained launch establishes loading, hardware fit and default-template mode on one machine. Neutral decision-protocol checks and quality measurements are separate evidence; neither valid setup metadata nor a closed template proves a diagnosis or repair correct, or a human time saving.
