# Optional local JS/TS code review

Decilist can reuse Ryan Yannelli's fine-tuned d1-3B reviewer through Python, TypeScript, HTTP, or a terminal command. The application supplies existing source text and its intended behavior. The trained review questions are bundled; no decision registration, form, or Playground is required.

This is an experimental **advisory** integration. It neither edits code nor approves a change. It is separate from the Python test-debugging experiment, whose overall time-saving gate failed.

## Set up once

From the repository, with a Python environment activated:

```bash
python -m pip install "./packages/python[liquid,server]"
python -m decilist review setup --accept-model-license
python -m decilist review serve --allow-model-code --device cuda --port 8766
```

The download is about 6.2 GB. Read the [pinned model license](https://huggingface.co/yannelli/d1-3b-code-review-decisions/blob/047a67d224c6d90767dddea3712ee8af434eaff3/LICENSE) first: the LFM Open License v1.0 has commercial-use conditions and is separate from Decilist's Apache-2.0 code. The runtime executes the pinned Liquid implementation only with the explicit `--allow-model-code` flag. Review loads cached weights offline; download happens in `setup`. A CUDA GPU is the tested target; CPU speed is not established.

The dedicated service listens only on `127.0.0.1:8766`. It loads weights once, serializes inference, disables request access logs, and sends no source to a cloud provider. This is a local development service, not an authenticated production endpoint.

To keep storage off C on this project's Windows machine, set these before setup or review:

```powershell
$env:HF_HOME = 'L:\CodexData\Decilist\work\model-cache'
$env:TEMP = $env:TMP = 'L:\CodexData\Decilist\tmp'
$env:PIP_CACHE_DIR = 'L:\CodexData\Decilist\cache\pip'
```

## Add it to an existing application

Build the TypeScript workspace with `npm ci` and `npm run build`. Packages are not yet published to npm or PyPI; use the repository workspace or a built package.

```ts
import { Decilist } from "@decilist/sdk";
const sdk = await Decilist.connect({ baseUrl: "http://127.0.0.1:8766" });
const review = await sdk.review(existingSourceText, { task: "Parse and validate the submitted order." });
existingEditor.showReview(review.findings, { incomplete: review.status === "unavailable" });
```

`existingSourceText` and `existingEditor.showReview` represent the host application's existing document and UI. Decilist supplies structured results, not an editor extension. The application can display a warning next to its existing save/review flow without adding a screen. Handle `unavailable` separately; never treat it as a clean review. JavaScript callers set `language: "JavaScript"`; the default is TypeScript.

Python can call the same service:

```python
from decilist import Client

async with Client("http://127.0.0.1:8766") as sdk:
    review = await sdk.review(existing_source, task="Parse and validate the submitted order.")
```

For an in-process Python host, load `CodeReviewer.load(allow_model_code=True, device="cuda")` once and await its `review` method. This still reviews JS/TS source; it is not a Python code reviewer.

To try an existing file without writing integration code:

```bash
python -m decilist review check packages/typescript/src/cli-args.ts --task "Split runtime options from application arguments at the separator." --url http://127.0.0.1:8766 --json
node examples/review_existing_file.mjs packages/typescript/src/cli-args.ts "Split runtime options from application arguments at the separator."
```

The source file is read, not executed. Exit code 0 includes advisory findings; unavailable review exits 2. No Git or editor hooks are installed automatically. Other languages can POST JSON to `/v1/code-review` with `code`, `task`, and optional `language` and `policy`. Calling from Java or C# does not extend the supported source languages.

## What the result means

The default rules are `error_handling_problem`, `type_safety_hole`, and `unsafe_input`. A finding requires model probability at least 0.9. These are model scores, not calibrated guarantees. Ten optional rules are available through `policy.rules`; opinionated style rules such as `noise_comments` are off by default.

- `flagged`: completed review with at least one finding.
- `no_flags`: completed review with no finding above the threshold; **not proof of correctness**.
- `unavailable`: timeout, provider error, or malformed output. Partial findings and coverage remain visible.

The result includes model/revision, probabilities, findings, timing, and reviewed/total line counts. Each finding identifies an 80-line context window, not a precise defect location. Windows overlap by 12 lines. Input is capped at 2,000 lines and 65,536 characters; unsupported or larger input is rejected, never silently truncated. Imports and other files are not retrieved. Cross-window dependencies can be missed. Keep compiler checks, tests, security tools, and human review.

## Reproduce the pilot

```bash
node benchmarks/code_review_tsc.mjs reports/code-review-typescript-pilot-01.json
python benchmarks/run_code_review.py --device cuda:1 --output reports/code-review-local-pilot-01.json
cd benchmarks/code-review-lint
npm ci --ignore-scripts
npm run measure
```

The model command requires both the pinned base Liquid model and specialist cached locally. It blocks Python socket connections during the run; `--models base` or `--models specialist` measures only one cached provider. The frozen fixture contains 18 newly authored snippets: nine issue/valid pairs, scored only on each snippet's declared target rule. The author supplied the labels. This is a small integration/transfer check, not independent validation, historical bug repair, or a measurement of developer time saved. The threshold was fixed before running the models.

TypeScript strict compilation and [recommended type-aware ESLint](https://typescript-eslint.io/users/configs/#projects-with-type-checking) are separate conventional baselines. ESLint dependencies and lockfile are isolated under `benchmarks/code-review-lint`; they are not SDK runtime dependencies. Reports retain individual lint diagnostics, since a lint message does not automatically prove the target issue was caught. No lint rules were disabled or added for these cases. No model replaces these tools.

## Observed pilot results, 28 October 2026

Same 18 frozen snippets, nine flawed and nine valid for the declared target rule; model threshold 0.9, unchanged between providers. Both models ran on an RTX 3090 with the vendor's explicit attention path. This is one pass per provider after warmup, not a throughput or concurrency benchmark.

| Method | Target issues detected / 9 | Valid snippets flagged / 9 | Median warm processing time |
| --- | ---: | ---: | ---: |
| TypeScript strict compilation | 0 | 0 | See raw compiler report; separate compiler processes |
| ESLint recommended, type-aware | 3 | 0 | 6.7 ms |
| General Liquid d1-3B with the same questions | 3 | 1 | 52.1 ms |
| Fine-tuned review specialist | 9 | 1 | 53.3 ms |

The specialist's target classification was 17/18 correct; neither model had an unavailable answer. ESLint diagnostics identify the target problems in `throw-string`, `any-record`, and `json-unvalidated`. The specialist additionally caught swallowed errors, failures reported as success, unchecked assertions, unchecked missing lookup results, unsafe HTML assignment, and SQL concatenation. It incorrectly flagged safe `textContent` assignment as unsafe input (0.9466). The threshold was **not** raised after observing this error.

The small authored pilot supports trying this specialist as an extra advisory layer. It does not establish performance on unseen repositories or developer time saved. The lint profile is one conventional configuration, not every possible static analyzer. Timings exclude model loading (about 24 seconds for the base in this run), do not include initial download, and compare different operations. One additional non-target warning on the flawed `any-record` case is retained in the raw results; target-rule metrics do not score it.

Evidence: [base model](../reports/code-review-base-pilot-01.json), [specialist](../reports/code-review-specialist-pilot-01.json), [ESLint](../reports/code-review-eslint-pilot-01.json), [TypeScript](../reports/code-review-typescript-pilot-01.json), [artifact hashes and unchanged vendor code](../reports/code-review-artifacts-01.json), [installed Python package check](../reports/code-review-package-01.json).

### Live integration and verification

The [live run](../reports/code-review-live-01.json) used an installed Python wheel and an installed TypeScript tarball outside the checkout, plus the installed CLI, against the actual specialist on loopback port 8766. All three reviewed the existing 14-line `cli-args.ts`. A 187-line SDK source file completed all three windows; its two warnings have not been independently adjudicated. Three simultaneous HTTP requests also completed. Source files were not executed or modified.

The first Python call, including connection and first model inference, took about 2.04 seconds. The subsequent standalone Node example took about 201 ms including process start; the Python CLI took about 689 ms including process start. These are individual smoke-test timings, not a human debugging experiment or reliable application latency distribution. The 53 ms pilot number describes warmed model processing on short snippets only.

Verification run: 2,041 Python tests plus 32 subtests passed in the full suite before the final CLI/coverage and snapshot-selection refinements. After those refinements, 67 focused review/runtime/compatibility tests passed. All 25 TypeScript SDK tests and the UI test passed; the existing cross-language contract check accepted ten Python envelopes. Wheel/sdist builds retained question definitions, attribution, and both applicable package licenses. The installed `review setup` command resolved all 16 required cached artifacts and rechecked the weight hash offline. Fresh network setup was not repeated after the initial staged download.

## Upstream credit and boundaries

- Specialist: [yannelli/d1-3b-code-review-decisions](https://huggingface.co/yannelli/d1-3b-code-review-decisions), revision `047a67d224c6d90767dddea3712ee8af434eaff3`.
- Original question definitions: [Ryan Yannelli's JS/TS dataset](https://huggingface.co/datasets/yannelli/js-ts-code-review-decisions/tree/5c3f3923f4a4278e249731ac3d3b613861efe3e4), CC BY 4.0. Definitions are unchanged; Decilist selects default rules and applies its own threshold, validation, coverage, and timeout policy. Attribution and license ship with the Python package.
- [Author's implementation report](https://ryanyannelli.com/blog/reviewing-every-coding-agent-edit-with-a-3b-decision-model/) is upstream evidence, not Decilist's measured result.

Decilist did not train these weights. Its contribution here is a ready-to-call contract, shared runtime, integration paths, visible failure states, and reproducible evaluation. No weights or vendor implementation are committed to the repository.
