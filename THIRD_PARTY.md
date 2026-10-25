# Third-party software and model terms

Decilist's original code is Apache-2.0. Dependencies and optional model artifacts retain their own licenses; installing an adapter does not relicense weights.

| Component | License / terms | Included in repository? |
| --- | --- | --- |
| Pydantic, FastAPI, Starlette, httpx, Uvicorn | Permissive upstream licenses (MIT/BSD variants); consult installed package metadata | Dependency declarations only |
| HTTPX 0.28.1 triage fixtures | BSD-3-Clause; [retained license](benchmarks/licenses/httpx-LICENSE.md) | Fixture mutations/tests; the lab copies pinned installed source only into isolated work directories and retains its license |
| Zod, TypeScript, Node.js types | MIT / Apache-2.0 upstream licenses | Dependency declarations and npm lockfile |
| LiquidAI/d1-3B weights and vendor code | [LFM Open License v1.0](https://huggingface.co/LiquidAI/d1-3B/blob/main/LICENSE), pinned revision in adapter | No |
| Transformers, PyTorch, torchvision, Pillow | Separate upstream licenses; optional runtime | No |
| OpenAI Decisions | Hosted service terms; future opt-in integration | No implementation, key, or model artifacts |

The Liquid license includes commercial-use conditions tied to annual revenue and redistribution/notice obligations. Its Section 5 and defined threshold must be checked for the intended entity and use. Do not describe the model weights as Apache-2.0 or claim unrestricted commercial rights. No weights or copied vendor implementation are redistributed here. The model can be downloaded directly from the vendor only after its terms and executable model code have been reviewed.

Source of model compatibility: [official model card](https://huggingface.co/LiquidAI/d1-3B). Source of future OpenAI interface: [official Decisions guide](https://developers.openai.com/api/docs/guides/decisions). These references were inspected October 25, 2026 and may change.
