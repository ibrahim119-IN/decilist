# Provider compatibility — checked October 25, 2026

| Provider | Implemented | Validation |
| --- | --- | --- |
| Uniform baseline | Yes | Unit, API, and shared contract tests |
| Custom local function / workflow rules | Yes | Unit, API, example, and smoke evaluation |
| LiquidAI/d1-3B | Adapter | Mapping/locking tests; no downloaded weights or live inference in the initial milestone |
| OpenAI Decisions | Planned | Official API documentation inspected; no adapter or paid request yet |

## Liquid local path

The official [model card](https://huggingface.co/LiquidAI/d1-3B) documents a custom `AutoModel` implementation and `system_one`, with `noul`, `choice`, and `score`. We map those to predict, choose, and score. It is not a chat-completions adapter. Do not assume generic Ollama, llama.cpp, or GGUF support from other Liquid models.

Pinned model/code revision: `051bcc464b01b9f92942b364d9586b0ef5912432`.

The loader resolves this complete local snapshot before constructing the model. This also ensures the vendor's lazy tokenizer resolves from the pinned snapshot instead of moving `main`. Downloads are off by default. Vendor Python code requires explicit opt-in. The model code's imports and adapter entry points were inspected; this is not a comprehensive security audit.

The development machine has two NVIDIA RTX 3090 cards with 24 GiB each. Capacity alone is not an inference compatibility result. CUDA wheels, native Windows support, model loading, memory usage, and warm decision latency still need to be measured.

### Next acceptance run

Use a dedicated environment and the official [PyTorch installation selector](https://pytorch.org/get-started/locally/) for a CUDA-compatible wheel. Then install the optional dependencies:

```bash
python -m pip install -e "packages/python[server,liquid]"
```

Read the [model license](https://huggingface.co/LiquidAI/d1-3B/blob/main/LICENSE) and pinned vendor source before running remote code. Download only that revision using `huggingface_hub.snapshot_download`. A programmatic first load can explicitly allow this initial download:

```python
from decilist import LiquidLocalProvider
provider = LiquidLocalProvider.load(
    allow_model_code=True, offline=False, device="cuda",
)
```

After the snapshot is cached, start the server offline. PowerShell:

```powershell
$env:DECILIST_PROVIDER = "liquid"
$env:DECILIST_ALLOW_MODEL_CODE = "1"
$env:HF_HUB_OFFLINE = "1"
python -m uvicorn apps.api.main:from_environment --factory --host 127.0.0.1 --port 8765 --no-access-log
```

Then run `python -m benchmarks.run --provider liquid --allow-model-code --output reports/local/liquid.json` in a separate process after stopping the server to avoid loading two copies. This path is documented for the next acceptance run, not certified by the initial foundation tests. If incompatible, record the failure and choose a supported runtime; do not rename another model as d1-3B.

## OpenAI later

Official [Decisions documentation](https://developers.openai.com/api/docs/guides/decisions) describes `POST /v1/decisions` and predicate/choice/score types. An adapter must map the actual response schema, preserve probabilities and usage, define cost accounting, and remain blocked by local-only unless explicitly allowed. Availability on this user's API account has not been tested. No key is stored in this repository.

## Offline claim

The baseline demo requires no network after dependency installation. A Liquid first-time download requires network. Offline learned inference is an acceptance criterion still pending. A local-only provider label does not prove process-level absence of network traffic.
