# Third-party software and model terms

Decilist's original code is Apache-2.0. Dependencies and optional model artifacts retain their own licenses; installing an adapter does not relicense weights.

| Component | License / terms | Included in repository? |
| --- | --- | --- |
| Pydantic, FastAPI, Starlette, httpx, Uvicorn | Permissive upstream licenses (MIT/BSD variants); consult installed package metadata | Dependency declarations only |
| HTTPX 0.28.1 triage fixtures | BSD-3-Clause; [retained license](benchmarks/licenses/httpx-LICENSE.md) | Fixture mutations/tests; the lab copies pinned installed source only into isolated work directories and retains its license |
| Zod, TypeScript, Node.js types | MIT / Apache-2.0 upstream licenses | Dependency declarations and npm lockfile |
| ESLint, @eslint/js, typescript-eslint | MIT upstream licenses | Separate benchmark dependency declarations and lockfile; no copied implementation and no SDK runtime dependency |
| LiquidAI/d1-3B weights and vendor code | [LFM Open License v1.0](https://huggingface.co/LiquidAI/d1-3B/blob/main/LICENSE), pinned revision in adapter | No |
| Ryan Yannelli's d1-3B code-review specialist | [LFM Open License v1.0 at pinned revision](https://huggingface.co/yannelli/d1-3b-code-review-decisions/blob/047a67d224c6d90767dddea3712ee8af434eaff3/LICENSE) | No weights or vendor code; optional explicit download |
| Ryan Yannelli's JS/TS code-review question definitions | [CC BY 4.0; retained license](packages/python/src/decilist/data/code_review_LICENSE.txt), dataset revision `5c3f3923f4a4278e249731ac3d3b613861efe3e4` | Original questions, license and attribution in Python package data; not relicensed Apache-2.0 |
| Transformers, PyTorch, torchvision, Pillow | Separate upstream licenses; optional runtime | No |
| Qwen3-Coder-30B-A3B-Instruct and ggml-org GGUF conversion | Apache-2.0 as declared by the [model publisher](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct) and [conversion publisher](https://huggingface.co/ggml-org/Qwen3-Coder-30B-A3B-Instruct-GGUF); pinned artifact in the explicit setup helper | No weights |
| Experimental Qwen3.6-27B and ggml-org Q8_0 GGUF conversion | [Apache-2.0 at the pinned base revision](https://huggingface.co/Qwen/Qwen3.6-27B/blob/6a9e13bd6fc8f0983b9b99948120bc37f49c13e9/LICENSE); [conversion metadata at its pinned revision](https://huggingface.co/ggml-org/Qwen3.6-27B-GGUF/blob/1132d03976d88e30a09ab0ddbf1531d5e1b46e7c/README.md) | No weights; runtime compatibility and repair quality require separate measured evidence |
| llama.cpp b11889 | [MIT](https://github.com/ggml-org/llama.cpp/blob/d854d98bae598388ecea1ed84396587e5edc326d/LICENSE); pinned official Windows binary downloaded separately | No binaries |
| Optional CUDA runtime DLLs | Separate upstream CUDA runtime terms; not covered by Decilist's Apache-2.0 license | No DLLs |
| OpenAI Decisions | Hosted service terms; future opt-in integration | No implementation, key, or model artifacts |

The Liquid license includes commercial-use conditions tied to annual revenue and redistribution/notice obligations. Its Section 5 and defined threshold must be checked for the intended entity and use. Do not describe the model weights as Apache-2.0 or claim unrestricted commercial rights. No weights or copied vendor implementation are redistributed here. The model can be downloaded directly from the vendor only after its terms and executable model code have been reviewed.

Source of model compatibility: [official model card](https://huggingface.co/LiquidAI/d1-3B). Source of future OpenAI interface: [official Decisions guide](https://developers.openai.com/api/docs/guides/decisions). These references were inspected October 25, 2026 and may change.
