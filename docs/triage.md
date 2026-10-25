# A next check after a failing test

Keep your normal editor, test runner, source search, and CI workflow. After a test fails, Decilist can read the saved failure and suggest one bounded next check. It does not establish the root cause, generate a repair, run commands from the log, edit source, or change tests.

The possible checks are dependency/environment, contract validation, decision policy, runtime lifecycle, application logic, and manual review. A model selects one of these fixed routes; the explanatory steps are authored by the application. Route confidence is not the probability that a diagnosis or repair is correct.

The [current frozen Python/pytest comparison](triage-results.md) has not shown a model benefit: the automated rules comparator selected an acceptable route under the frozen labels for all eight regression cases, while the local model selected one and abstained on seven. Both cases reserved for the human pilot currently lead to manual review. The pilot is measurement-readiness work, not proven assistance; a meaningful future efficacy run needs a prespecified improved assistant and fresh cases. The current threshold and cases remain unchanged.

## Use your existing failure log

Use the Python SDK environment from [runtime setup](runtime.md) and an already configured learned local runtime. Provider/model setup happens once; importing triage does not download or start a model. The default rules demo is deliberately refused as learned triage.

After your usual failed pytest or CI run has produced `failed.log`, run one command from your project:

```bash
python -m decilist.triage --log failed.log --project .
```

The command shows the next check, status, route confidence, observed failure lines, and referenced source locations together. It reads the saved log; it does not rerun that original command. Its exit status is the status of this suggestion, not the exit status of your earlier tests. Use `--format json --output triage-result.json` when you need the raw decision and prepared evidence in a new local file; existing output files are not overwritten.

The SDK's default endpoint is `http://127.0.0.1:8765`; an existing `DECILIST_URL` setting can choose another loopback runtime. Remote endpoints are rejected before evidence is transmitted. An unavailable model is reported, with no silent rules or cloud fallback.

## Call directly from developer tooling

This uses the same read-only preparation and bounded decision as the CLI:

```python
import asyncio
from pathlib import Path
from decilist import Client
from decilist.triage import diagnose, format_text
from decilist.triage_evidence import prepare_evidence

async def main():
    evidence = prepare_evidence(
        Path("failed.log").read_text(encoding="utf-8"),
        project=Path("."),
    )
    async with Client("http://127.0.0.1:8765") as client:
        report = await diagnose(evidence, client=client)
    print(format_text(report), end="")

asyncio.run(main())
```

The optional `command` argument to `prepare_evidence` is metadata. Supply it only when it is the command that actually produced the log; neither the builder nor `diagnose` executes it. The client remains owned by the caller. An in-process integration can supply an explicitly local client implementing the same bounded decision contract.

`suggested` means a non-review route met the current policy. `needs_review` means uncertainty, manual review, or insufficient diagnostic detail. `unavailable` means the runtime or response could not support a suggestion. Empty or passing logs are rejected; a nonzero exit code without recognizable failure details cannot justify a specific check. Raw decisions and attempt traces remain available for inspection.

## What the evidence supports

Preparation clips the log, removes common credential patterns, and reads at most three source excerpts from recognized references inside the resolved project. It does not scan the repository, infer unseen execution, or treat instructions inside a traceback as authority. Unsafe/outside paths and unsupported file types do not become source excerpts. Redaction is heuristic; inspect sensitive logs before using them, including with a local model.

A traceback can identify a failing boundary while leaving the cause uncertain. Missing environment details, incomplete logs, or source changed after a run can weaken the evidence. The suggested route should guide the next inspection; the developer still checks the actual input, expected behavior, and code before making a change.

The end-to-end executable corpus currently covers Python/pytest. JavaScript/TypeScript source-reference parsing has unit coverage; that does not establish diagnosis quality for Jest, Vitest, or another runner. The core TypeScript SDK continues to provide the general decision calls, with no new TypeScript triage convenience API.

Your existing tests remain the acceptance check. A suggestion does not authorize weakening an assertion or changing a policy merely to make a run pass. After an intentional source repair, rerun the original tests and relevant regression checks through your normal tools.

The [isolated triage lab](triage-study.md) provides a separate, deferred two-case timing pilot. Machine routing accuracy and request latency do not measure human repair time. The pilot records no human duration until a participant explicitly starts it, and two cases cannot establish general developer speedup.
