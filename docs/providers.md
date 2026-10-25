# Provider compatibility — checked October 25, 2026

| Provider | Implemented | Validation |
| --- | --- | --- |
| Uniform baseline | Yes | Unit, API, and shared contract tests |
| Custom local function / workflow rules | Yes | Unit, API, example, and smoke evaluation |
| LiquidAI/d1-3B | Yes | Real Windows CUDA inference, both HTTP SDKs, repeated offline smoke, frozen 60-case evaluation; quality limits recorded |
| OpenAI Decisions | Planned | Official API documentation inspected; no adapter or paid request yet |

## Liquid local path

The official [model card](https://huggingface.co/LiquidAI/d1-3B) documents a custom `AutoModel` implementation and `system_one`, with `noul`, `choice`, and `score`. We map those to predict, choose, and score. It is not a chat-completions adapter. Do not assume generic Ollama, llama.cpp, or GGUF support from other Liquid models.

Pinned model/code revision: `051bcc464b01b9f92942b364d9586b0ef5912432`.

The loader resolves this complete local snapshot before constructing the model. This also ensures the vendor's lazy tokenizer resolves from the pinned snapshot instead of moving `main`. Downloads are off by default. Vendor Python code requires explicit opt-in. The model code's imports and adapter entry points were inspected; this is not a comprehensive security audit.

The measured environment uses the second NVIDIA RTX 3090 (`cuda:1`, 24 GiB), Windows 11, Python 3.12.0rc2, torch 2.13.0+cu126, torchvision 0.28.0+cu126, transformers 5.14.1, and Hugging Face Hub 1.33.0. These are recorded installed versions; new environments should use a supported stable Python release. The local snapshot has 15 files totaling 6,265,052,644 bytes; all pinned file hashes passed. Model weights are 6,247,065,504 bytes, SHA-256 `50e03317847caf6df9a9aee27ed40f20554a86a21e60d1d47ba41a422b546c0c`.

The Windows torch build lacks compiled Flash Attention. The provider checks this capability and selects the pinned vendor's existing explicit attention calculation in memory. It preserves the device, model, vendor source files, and supported builds' default path. Twelve actual small-tensor comparisons with independent dense attention passed. This is compatibility work, not a new model or a claim to Flash Attention speed. The benchmark records `attention_backend: vendor-explicit`.

Cached model load measured about 5.3 seconds in the repeated smoke; held-out sequential p50/p95 were 45.63/47.76 ms for this narrow text workload. Benchmark peak PyTorch GPU allocation was 6,357,245,440 bytes. Initial parallel setup took about 46.9 observed minutes, excluding earlier environment creation; downloads overlapped. See [full measurements and failures](model-validation.md), including the limited 66.7% accepted classification accuracy. Performance on other hardware or Linux CUDA is unverified.

### Reproduce setup and measurements

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

After the snapshot is cached, start the server offline. Select the device present on your machine; `cuda:1` is the second GPU used for the recorded run. PowerShell:

```powershell
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
python -m decilist runtime start --provider liquid --allow-model-code --device cuda:1
```

Stop the server before running the following checks to avoid loading two copies or contaminating measured timings. Choose new output paths to preserve prior evidence:

```bash
python scripts/verify_liquid.py --device cuda:1 --output reports/local/new-smoke.json
python -m benchmarks.run_text --provider liquid --allow-model-code --device cuda:1 --split all --repetitions 3 --output reports/local/new-text-evaluation.json
```

The simpler launcher/setup path is documented in [runtime setup](runtime.md). A setup or inference failure must remain visible; do not substitute a different model or rules under the Liquid provider name.

## OpenAI later

Official [Decisions documentation](https://developers.openai.com/api/docs/guides/decisions) describes `POST /v1/decisions` and predicate/choice/score types. An adapter must map the actual response schema, preserve probabilities and usage, define cost accounting, and remain blocked by local-only unless explicitly allowed. Availability on this user's API account has not been tested. No key is stored in this repository.

## Offline claim

The baseline demo requires no network after dependency installation. A Liquid first-time download requires network. Two cached learned-inference smoke runs succeeded with Hub offline flags and Python `socket.connect`/`connect_ex` blocked, recording zero connection attempts. This is a bounded application-level check, not process-level packet capture or an OS firewall test. A local-only provider label alone does not prove absence of network traffic.
