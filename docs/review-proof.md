# Proof-first local JavaScript review

`decilist review investigate` reviews an existing small JavaScript project, tries to reproduce a proposed defect, and retains an isolated repair candidate for inspection. It is **experimental and advisory**. Integration is one command or a Python call; no Playground, form, decision registration, or editor extension is required.

For each proposed hypothesis, the engine:

1. Creates a baseline from an immutable text snapshot and requires the existing regression tests to pass with active tests.
2. Requires an actual tracked assertion failure in the proposed reproduction before requesting a repair. A syntax error, import error, timeout, or zero assertions does not qualify.
3. Builds a fresh candidate from the same snapshot, allowing only hash-guarded edits to selected source lines inside the cited evidence range. The default limit is two edits and 30 changed lines.
4. Requires the **same reproduction** to pass, then requires the unchanged existing regressions to pass.

The source and tests in your project are never edited. The CLI creates a `.decilist-review` output directory inside the project.

**A failing model-written expectation is not proof that the expectation follows the public contract.** An accepted candidate has passed these execution gates; it can still encode an invented requirement or miss another regression. Check the claim against the documented input domain and use additional independent checks before applying a patch. There is no demonstrated human time-saving claim.

## Try the tiny project

Use Python 3.11+ and Node with the built-in test runner. From this repository, install the current Python package into your existing environment:

```sh
python -m pip install -e ./packages/python
node --test --test-reporter=tap examples/review_proof/test/within.test.mjs
```

The [example](../examples/review_proof/README.md) is an authored teaching fixture with incomplete existing tests and a documented public contract. Its behavior is not benchmark evidence.

An actual installed-wheel run on the presentation machine found the lower-endpoint defect, retained a one-line candidate, and passed its reproduction plus the four existing tests. The [recorded run](../reports/code-review-proof-03/installed-demo/verification.json) is a smoke test; model outcomes on other projects are not guaranteed.

You must already have a local OpenAI-compatible model server that supports the requested JSON-schema structured output. This command does not download weights or start a server. The following values match the optional [Qwen3.6 local setup](stronger-local-profiles.md); use the URL and model alias of your running server. `--no-thinking` enables the current llama.cpp/Qwen3.6 structured-output compatibility settings.

Run this single command from the repository root:

```sh
python -m decilist review investigate examples/review_proof --source within.mjs --entry within.mjs --regression test/within.test.mjs --contract README.md --task "Check within(value, min, max) against its documented inclusive numeric interval." --model-url http://127.0.0.1:8794/v1/chat/completions --model qwen36-27b-q8 --no-thinking --allow-execution
```

For your own project, replace the project directory, relative file paths, and task:

| Argument | Meaning |
| --- | --- |
| `--source` | Existing editable source files shown to the model. Tests cannot be edited. |
| `--entry` | Public module paths the reproduction may load; defaults to the selected sources. |
| `--regression` | Existing Node test files; the host runs `node --test --test-reporter=tap` on them. |
| `--contract` | Existing public API documentation within the project. If omitted, the task supplies the contract. |
| `--task` | The intended public behavior to investigate. State the supported input domain. |
| `--allow-execution` | Explicitly permits local execution of the trusted project and generated probes in disposable copies. |

All file arguments are relative to the project directory. Source, entry, and test files must be native Node `.js`, `.mjs`, or `.cjs` files. Use a small, self-contained project: the CLI collects bounded UTF-8 text files, skips dotfiles, dependencies, lockfiles, and common build directories, and does not install dependencies. It permits at most 256 files, 2 MB total, and 512 KB per collected file; the contract must fit in 16,000 characters. TypeScript compilation and custom test commands require your own host integration.

## Inspect the result

The CLI prints JSON and writes artifacts under:

```text
<project>/.decilist-review/investigate-<unique-id>/
  report.json
  model/                    exact model request/response files
  workspaces/               retained baseline and candidate copies
  candidate.patch           only when a candidate is accepted
```

Read `status`, `reason`, and the observations in `attempts` in `report.json`. `candidate_workspace` is populated only for an accepted candidate; the report's `patch` field points to its unified diff. The persisted reproduction is in each workspace's `.decilist-proof/` directory. Review the reproduction, the cited public contract, and the patch together.

Every result also reports `contract_validation: "not_performed"` and `review_required: true`. Executing a model-written assertion cannot certify that its expectation is required by your application. See the [measured historical failures](code-review-proof-results.md) before adopting the workflow.

| Status | Interpretation |
| --- | --- |
| `accepted_candidate` | A tracked assertion failed before the edit; the same reproduction and existing tests passed afterward. Requires review. |
| `unproven` | No hypothesis, no qualifying baseline failure, or the candidate did not pass its reproduction. No accepted patch. |
| `regression` | The candidate passed its reproduction but failed existing regressions. |
| `invalid` | Input, model evidence, or proposed edits failed validation. |
| `infrastructure_failure` | Provider, process, baseline checks, integrity, or timeout prevented validation. |

Exit code `2` signals `invalid` or `infrastructure_failure`; `0` also includes `unproven` and `regression`. Exit code zero is not approval or evidence of a fix. The command never applies a patch to the original project.

## Embed in an existing Python flow

This uses the same engine and local host. Calling the host is your application's deliberate execution opt-in; the SDK has no separate `--allow-execution` flag. For the tiny project, the complete input snapshot is three files:

```python
import asyncio
import tempfile
from pathlib import Path

from decilist import RepairPolicy, ReviewRepairEngine
from decilist.review_repair_local import LocalReviewProvider, NodeReproductionHost

async def inspect_project():
    project = Path("examples/review_proof").resolve()
    paths = ["within.mjs", "test/within.test.mjs", "README.md"]
    sources = {p: (project / p).read_bytes().decode("utf-8") for p in paths}
    output = project / ".decilist-review"
    output.mkdir(exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="sdk-", dir=output))
    provider = LocalReviewProvider(
        source_paths=["within.mjs"], public_docs=sources["README.md"],
        endpoint="http://127.0.0.1:8794/v1/chat/completions",
        model="qwen36-27b-q8", disable_thinking=True,
        evidence_dir=run / "model",
    )
    host = NodeReproductionHost(
        entry_paths=["within.mjs"], timeout_s=15,
        regression_argv=["node", "--test", "--test-reporter=tap", "test/within.test.mjs"],
    )
    return await ReviewRepairEngine(
        provider, host, RepairPolicy(max_hypotheses=2, timeout_ms=180000),
    ).run(sources, task="Check the documented inclusive numeric interval.",
          allowed_paths={"within.mjs"}, scratch_root=run / "workspaces")

result = asyncio.run(inspect_project())
print(result.model_dump_json(indent=2))  # Show this in your existing review flow.
```

The SDK returns a `RepairResult` and retains workspaces/model traces configured above. Unlike the CLI, this snippet does not write `report.json` or a patch. Keep every runtime dependency needed by your trusted entries available in your own snapshot/host setup.

## Execution boundary

Run this local host only on **trusted, reviewed JavaScript modules and tests**. Their imports execute normally with your Node privileges. The VM, capability filters, allowlisted entries, output limits, and process timeouts are defense in depth, **not an OS sandbox**. Hostile code requires a separately provisioned OS sandbox around the entire host.

The CLI uses a literal loopback model endpoint, at most two hypotheses, a 180-second overall cap, and 15-second process timeouts. It makes one model request per stage, with no retries or cloud fallback. Limits can stop useful work and do not prove code safe. Traces include selected source, the contract, and model output; review them before sharing. Existing passing tests and this fixture do not establish general repair accuracy, security, or developer productivity.
