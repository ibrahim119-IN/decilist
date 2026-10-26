# Natural historical-transfer measurement

These modules integrate the root-reviewed measurement draft. Their tests use made-up arithmetic fixtures and stub providers; integration did not import the real natural bridge, prepare a natural trial, resolve sealed selectors, inspect sealed cases or call a model. Existing V6 code, matrix, trials, sources and protocol remain unchanged. The root must freeze an actual natural-transfer candidate before case exposure.

The authoritative protocol is the active repository's `docs/triage-natural-transfer-protocol.md`. The fixed order is NAT-01 normal, NAT-03 assisted, NAT-04 normal, NAT-01 assisted, NAT-03 normal, NAT-04 assisted. NAT-02 cannot receive strict-transfer credit.

## Modules and boundary

`natural_candidate.py` snapshots exact product/helper code, the measurement Python files, current protocol bytes, stronger model configuration, actual native readiness/mode evidence, and the author's **safe** bridge freeze/code identity. Candidate construction never imports the bridge or opens any case. Native metadata must be actual previously captured evidence from the preselected stronger closed-thinking profile, not an inferred label.

`NaturalLab` adapts the author's neutral `worker_bridge_api.py` methods. It uses only the approved safe interface and these post-freeze methods: `verify_identity`, `prepare_case`, `run_case(..., group='all')`, and `validate_repair`. It never invokes `--reference`, requests a reference tree, renames checks, installs a dependency, or injects a plugin. Its sidecar manifest, original-source audit copy, ledger and model artifacts live outside the participant project. The actual historical project keeps its full `src` root, immutable tests/configuration/licenses and the already-proven licensed vendor dependency.

`natural_trial.py` uses the existing real wall/monotonic/boot identity clock and the same minimal action ledger with a 900-second cap. The assisted arm makes one initial `run_triage_v6(..., apply=True, context_policy='v6')` call inside the clock. Native profile preflight, workflow retries/waiting/stages, application and failed assistance remain included. Successful application immediately performs the independent full protected verification in the same command. Failure or abstention permits ordinary manual continuation in the assigned arm. A runtime timeout/error is retained without substituting another model or hint.

`report_natural_trials.py` reruns the full expected protected collection after the clock and checks original canonical hashes, actual source diffs, protected tests/vendor/licenses/configuration, source stability, collection identity, execution outcomes, assignment, clocks and initial-hint provenance. It retains missing/duplicate/failed records. Initial assistant repair credit requires a real first protected passing verification matching the applied hint's source, without later recorded manual work. A model claim or citation is insufficient.

The report evaluates the predeclared engineering gate and uses the descriptive median across **all three assigned finished slots per arm**, including unsolved/capped outcomes. It does not select a median from successful cases alone. Missing, invalid or unfinished slots make that median unavailable and cannot yield a favorable gate. These remain three paired historical defects, not six independent human tasks; human time savings remain unmeasured.

## Root-only setup after review, before any exposure

Create a private natural profile manifest. It can retain an unused `current` entry from the V6 manifest, but `stronger` is mandatory and is the sole selected treatment:

```json
{
  "schema_version": 1,
  "bridge_freeze_path": "L:/.../natural-regression-research/bridge-freeze-safe.json",
  "bridge_path": "L:/.../natural-regression-research/worker_bridge_api.py",
  "profiles": {
    "stronger": {
      "config_path": "stronger-model.json",
      "endpoint": "http://127.0.0.1:8792",
      "protocol_metadata": {}
    }
  }
}
```

Populate `protocol_metadata` from the actual native profile capture plus measured weights/executable/launch evidence. Paths may be absolute or relative to this manifest. Do not create placeholders and treat them as verified evidence. The safe bridge descriptor includes only opaque identities, counts and broad roots. Its private registry is not a candidate-builder input.

Use the normal repository development environment with its Python package installed. Freeze with exclusive creation:

```python
from pathlib import Path
from benchmarks.natural_candidate import candidate_snapshot, _json_bytes
with Path("NEW_NATURAL_CANDIDATE.json").open("xb") as output:
    output.write(_json_bytes(candidate_snapshot(Path("NATURAL_PROFILES.json"))))
```

The root must review the final integrated files and freeze the candidate before preparation. The measurement-source snapshot explicitly covers the three active measurement modules. Moving/editing measurement files or changing bridge/profile/code/protocol afterward changes candidate identity. Do not reuse an exposed trial as fresh after an implementation correction.

After freeze, the root can use `prepare_trial` or `prepare_trial_async` with an explicit `NaturalLab` or the CLI's checked bridge loader. Record directories must be new children of the bridge-owned research root's `agent-trials`; the bridge independently enforces owned project/evidence destinations. A prepare command is evaluator-only; do not give its case data to an agent before its clock starts.

```text
python -m benchmarks.natural_trial prepare --trial NEW_RESEARCH_AGENT_TRIAL --slot 2 --agent-id FRESH_AGENT --candidate-freeze CANDIDATE.json --profiles NATURAL_PROFILES.json
python -m benchmarks.natural_trial start --trial NEW_RESEARCH_AGENT_TRIAL
python -m benchmarks.natural_trial hint --trial NEW_RESEARCH_AGENT_TRIAL --apply --format text
python -m benchmarks.natural_trial attempt-start --trial NEW_RESEARCH_AGENT_TRIAL --phase repair --command "Actual observed action"
python -m benchmarks.natural_trial attempt-end --trial NEW_RESEARCH_AGENT_TRIAL --id RECORDED_ID --exit-code 0
python -m benchmarks.natural_trial verify --trial NEW_RESEARCH_AGENT_TRIAL
```

Ordinary tools receive no hint. Assisted success needs no separate verify command; unsuccessful assistance can continue manually. Participants must remain restricted to their single project and the neutral measurement commands; the filesystem layout and local ledger do not prove absence of unrecorded reads. No private evaluator/reference path is sent to the model.

For an independent root export:

```text
python -m benchmarks.report_natural_trials --trials-root RESEARCH_AGENT_TRIALS --profiles NATURAL_PROFILES.json --candidate-freeze CANDIDATE.json --output NEW_PUBLIC.json --private-output NEW_PRIVATE.json
```

Exports are separate, redacted public and full private records, and existing files are refused. Review raw diagnostics before publishing. Root bridge verification, setup/download/loading and warm agent timing are distinct evidence.

## Stub-only verification

Use the existing interpreter; do not install or change the main environment. From the repository in PowerShell, using the normal development environment:

```powershell
$env:TEMP = 'L:\CodexData\Decilist\tmp'
$env:TMP = 'L:\CodexData\Decilist\tmp'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
& 'L:\CodexData\Decilist\work\.venv\Scripts\python.exe' -m pytest -p pytest_asyncio.plugin -o asyncio_mode=auto -q tests/test_natural_measurement.py
```

The explicit test options make plugin loading and async behavior deterministic. They do not affect participant checks or the frozen workflow.
