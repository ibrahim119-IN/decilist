# Explicit local coding-model experiment

This experimental path connects the Python SDK to a caller-owned llama.cpp service. Importing the SDK never downloads weights, launches a process, or changes the existing Liquid runtime. The ordinary `decilist triage` command remains the V2 assistant. V3 is selected explicitly below.

The selected artifact is Qwen3-Coder-30B-A3B-Instruct Q4_K_M, from `ggml-org/Qwen3-Coder-30B-A3B-Instruct-GGUF`, revision `0ca51122012fe4a20dc82988ee58780b008ce4e3`. The file is 17,988,419,552 bytes. Its SHA-256 is `f6050b4da22869aa3fb9d94bb3314e9fc5a027d4741c1f97761c387d6b2b2335`. The setup helper pins llama.cpp b11889 and verifies the model and executable before serving. These setup identities are separate from evidence of useful diagnoses.

For the explicit Qwen3.6-27B profile and L: storage settings, see [local model profiles](stronger-local-profiles.md). The existing Coder defaults below remain available. V6 requires `--context 16384` and its separate [one-time identity setup](triage-v6-quickstart.md).

## Windows CUDA setup

The helper targets Windows x64 CUDA. The HTTP provider itself is Python code, but compatibility with other binaries/platforms needs its own verification. The current experimental configuration requests one GPU, 8,192 context tokens and one inference slot; it does not promise to fit an ordinary laptop. Keep at least 3 GB spare disk space in addition to the weights and runtime artifacts.

From the repository, use an isolated Python environment:

```powershell
python -m pip install -e ./packages/python
python -m pip install huggingface-hub==1.33.0
python scripts/setup_stronger_local.py download --cache-dir ./work/qwen
python scripts/setup_stronger_local.py serve --cache-dir ./work/qwen --gpu 0
```

The download is an explicit internet operation. Serving verifies local files and uses the runtime's offline flag. This is not an operating-system network-isolation claim. CUDA DLLs can instead be supplied explicitly with `--cuda-dll-dir`; the helper records their hashes. That option does not establish their origin or license by itself. No Torch installation is needed when using the helper's separately downloaded CUDA runtime archive.

Keep the serving terminal open. It writes `decilist-model.json` after the model, build, alias and owned listener pass readiness checks. Ctrl+C stops only the process launched by this helper. An occupied port is an error; the helper does not stop or adopt another service.

## Use an existing failure log

In another terminal using the installed SDK:

```powershell
python -m decilist.triage_v3 --log failed.log --project . --model-config ./work/qwen/decilist-model.json --design diagnostic
```

The command returns a testable hypothesis, supplied source references, a next inspection, an expected observation and an alternative. It reads bounded project context; it does not execute tests or apply repairs. `grounded_hypothesis` means the output cited supplied references and passed structure checks. It does not mean the hypothesis was proven correct. Error, truncated-output and review states retain the failure evidence.

Use `--design route` for the six generic inspection areas, or `--design action` for a choice among at most five grounded inspections plus manual review. These ranked designs keep the illustrative 0.7 acceptance threshold. Native Liquid decision probabilities and Qwen alias-conditioned probabilities are uncalibrated; their numeric values are not equivalent confidence in correctness. The bounded generative diagnostic returns no confidence percentage.

No Playground, new application form, decision registration or cloud account is required. These commands are an optional debugging application above the general decision SDK.

## Embed the general provider

The model config is generated once by the explicit setup helper. The provider accepts a numeric loopback address, checks model/build/template identity before sending evidence, and closes only its own HTTP connection:

```python
import asyncio
from pathlib import Path
from decilist import Decilist, Policy
from decilist.llama_cpp import LlamaCppProvider
from decilist.triage_v3 import load_qwen_config

async def main():
    config = load_qwen_config(Path("work/qwen/decilist-model.json"))
    async with LlamaCppProvider(**config) as provider:
        result = await Decilist([provider]).choose(
            "The retry budget is exhausted and no response was received.",
            "Choose the next application action using the observed state.",
            {"retry": "Retry if budget remains", "review": "Stop retrying and request review"},
            policy=Policy(local_only=True, min_confidence=0.7),
        )
        print(result.value if result.status == "accepted" else "Needs review")

asyncio.run(main())
```

Probability extraction masks every vocabulary token except the complete set of single-token option aliases. It validates all returned aliases and their total mass, rather than inventing missing probabilities or treating a greedy token as confidence one. This adds local JSON/HTTP overhead. `predict` and `score` use the same provider contract.

## Use the existing HTTP or TypeScript client

The source checkout includes an explicit HTTP example above the same provider. Install the server extra, keep the model service running, then open another terminal:

```powershell
python -m pip install -e './packages/python[server]'
python -m examples.llama_http --model-config ./work/qwen/decilist-model.json --port 8793
```

Point the existing Python or TypeScript HTTP client at `http://127.0.0.1:8793`. It uses the existing `/health` and `/v1/decisions` contract, so application code does not need to call llama.cpp itself. Startup checks the model identity before accepting decisions. The example has no Playground and owns only its HTTP connection; stopping it does not stop the model service. This is an explicit source-checkout example, not a new provider option in the ordinary runtime launcher. Stub-based lifecycle tests are separate from live model compatibility and quality measurements.

## Evaluation scope

See the [predeclared V3 experiment](triage-v3-experiment.md) and [actual results](triage-v3-results.md). The real model passed [five provider checks](../reports/triage-v3-provider-smoke-2026-10-26.json), and the existing Python/TypeScript clients passed [six live HTTP checks](../reports/triage-v3-http-live-2026-10-26.json). Those checks establish integration, not diagnostic usefulness. On six fresh seeded regressions, Qwen action targeting matched four versus Liquid's two and the no-model baseline's three. The initial diagnostic outputs did not explain any complete distinguishing mechanism under the declared manual rubric. Eight fresh agent repair runs on two reserved defects were all successful but slower with assistance. An explicitly exploratory full-test-context replay improved one diagnosis; it is not fresh holdout evidence. This assistant remains experimental.

The comparison separates raw ranking, policy acceptance, source-target availability and bounded diagnostic output. Seeded regressions in pinned real projects are not naturally occurring production incidents. Repair participants are fresh agents, not human developers. No human time-saving claim follows from these measurements. Model licenses and runtime dependency terms remain separate in [THIRD_PARTY.md](../THIRD_PARTY.md).
