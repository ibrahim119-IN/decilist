# V4 quickstart: triage an existing Python project

V4 reproduces an explicitly selected test failure, gathers runtime and source context, asks a local coding model for a hypothesis and a small source intervention, and tests that intervention in a disposable copy. No Playground session or new project setup is required.

V4 is experimental. A passing intervention supports the hypothesis under the selected tests; it does not prove a complete causal explanation or production correctness. The [retained V4 evaluation](triage-v4-results.md) found no protected repairs on six fresh evaluation defects and slower assisted agent repairs on four reserved defects. Human developer-time benefit remains unmeasured; this guide makes no improvement claim.

## 1. Prepare the environment and model identity

Use Python 3.11 or newer with this Decilist checkout, `pytest`, `pytest-asyncio`, and the target project's test dependencies installed in the same environment. From the Decilist repository root, an editable development install is:

```powershell
python -m pip install -e "./packages/python[dev]"
```

Start a compatible local llama.cpp server separately. V4 connects to the already running server; it does not start a server, load a model, or download weights. The default endpoint is `http://127.0.0.1:8792`; use `--url` in the CLI or `endpoint=` in Python to change it. The endpoint must use a numeric loopback address.

Create a JSON identity file, replacing these placeholders with the exact configured model ID and server `build_info`:

```json
{
  "model": "your-server-model-id",
  "expected_build": "exact-server-build-info"
}
```

`expected_model_path` is the only optional JSON field and, when supplied, must match the server's reported model path exactly. A mismatched identity or unavailable model leaves the result unverified. The file contains identity settings, not model weights.

## 2. Select existing tests and a fresh work directory

Choose at least one failing selector and at least one existing regression selector. Paths and pytest node IDs are relative to the target project, for example `tests/test_service.py::test_bug`. A test file or test directory is also accepted. Repeat `--test` and `--regression` for multiple selectors.

Choose a **new** `--work-dir` outside the target project, under an existing parent directory you own. For example, create `L:/triage-runs` yourself, then use `L:/triage-runs/my-app-001` as the new work directory. V4 refuses an existing destination and retains its owned snapshot and evidence there. Use a different destination for each run.

Tests execute Python with your operating-system permissions. The disposable copy is not an OS sandbox; use only projects and tests you trust. Virtual environments, caches, known credential paths, and other excluded or oversized files are not copied. Install required test dependencies beforehand and inspect the report's `excluded` entries if reproduction fails.

## 3. Run the CLI

This command tests an intervention without applying it to the original project:

```powershell
python -m decilist.triage_v4 --project L:/work/my-app --test tests/test_service.py::test_bug --regression tests/test_existing.py --work-dir L:/triage-runs/my-app-001 --model-config L:/work/model-identity.json
```

The default permits implementation edits under `src`, `packages/python/src`, and `vendor`. For another source layout, repeat `--production-root` with project-relative implementation directories. A flat-module project can explicitly use `--production-root .`; test and configuration files remain protected.

Add `--capture-failure-text` to include dynamic assertion and exception text in the model context. Without it, the trace retains structural failure information and omits dynamic failure values. Raw pytest stdout and stderr are retained locally in either mode and can contain printed values.

For unmarked async tests that require pytest-asyncio auto mode, pass the permitted `-o asyncio_mode=auto` option explicitly. The CLI has no dedicated async switch or general pytest passthrough: each option token is another value in the relevant repeated list. Append these arguments when both selections need auto mode:

```powershell
--test=-o --test=asyncio_mode=auto --regression=-o --regression=asyncio_mode=auto
```

The equals sign in `--test=-o` and `--regression=-o` keeps `-o` as a value for the workflow. Arbitrary pytest configuration and plugin options are rejected. The original selectors and permitted options are reused unchanged for verification.

To authorize applying a verified candidate during this run, add `--apply` **before running the command**. V4 then rechecks the original source hashes and paths and transfers only verified production edits. A changed original project rejects application. Individual file replacement is atomic, with best-effort rollback across files. Tests and configuration are never patch targets. There is no separate apply-an-existing-report command; another invocation runs a new experiment and needs a new work directory.

## 4. Inspect the result or call the Python API

The CLI prints a hypothesis, concise test evidence, a unified diff when available, and the evidence path. Add `--format json` for the full report. Once the snapshot exists, evidence is retained under `WORK_DIR/evidence/`, including `workflow.json`, `context.json`, generation responses, candidate diffs, and per-run test output, traces, and audits.

The report includes stage wall times and per-attempt timings, including failed operations and a fresh feedback snapshot. `timings_ms.total` includes the final source-integrity check and stops immediately before final report persistence. An enclosing benchmark or trial wall clock also includes that persistence.

| Result | Meaning |
| --- | --- |
| `state == "hypothesis_supported_by_tests"` | The baseline reproduced a real failure; the candidate passed every collected target and regression test, with unchanged test collection and protected-file hashes. |
| `state == "unverified"` | Verification did not establish those conditions. Check `reason`, `errors`, and `attempts`; this includes a non-failing baseline, runtime/model failure, rejected intervention, or failing tests. |

`apply.applied` records whether the original project was actually changed. A supported hypothesis can still have `apply.applied == false`, including when application was not requested or a later source change rejected it. The CLI exits zero only for a supported result and, if `--apply` was requested, successful application.

The asynchronous Python API accepts the same selectors and permitted pytest options as ordinary list items:

```python
import asyncio
from pathlib import Path

from decilist.triage_v4 import run_triage_v4


async def main():
    report = await run_triage_v4(
        project=Path("L:/work/my-app"),
        tests=["tests/test_service.py::test_bug"],
        regressions=["tests/test_existing.py"],
        work_dir=Path("L:/triage-runs/my-app-api-001"),
        model_config=Path("L:/work/model-identity.json"),
        capture_failure_text=False,
        apply=False,
    )
    print(report["state"], report["reason"])
    print(report.get("report_path"))


asyncio.run(main())
```

For async auto mode, use, for example, `tests=["tests/test_async.py::test_bug", "-o", "asyncio_mode=auto"]` and add the same option pair to `regressions` if needed. Python uses `allowed_production_roots=["."]` for the CLI's explicit flat-module permission. In an existing event loop, await `run_triage_v4` directly instead of calling `asyncio.run`.

Each workflow makes at most two model generations. A second attempt is allowed only after an actual candidate test rejection and starts from a fresh source snapshot. Generation and verification guards, and unavailable runtimes, leave the intervention unverified. See the [V4 experiment protocol](triage-v4-experiment.md) for evaluation boundaries and the distinction between test support and a measured developer benefit.
