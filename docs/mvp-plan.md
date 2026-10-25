# Decilist implementation plan

Updated October 25, 2026. Presentation target: Sunday, November 1, 2026. All dates use Africa/Cairo. These dates allocate work; they do not schedule automatic runs.

## Product outcome

A developer adds a bounded decision to an existing application through a direct SDK call. Decilist handles provider differences, answer validation, uncertainty policies, and evaluation. The application keeps its own interface, architecture, and action logic.

The primary journey is **install the SDK, connect a provider once, call choose/predict/score**. A question and its allowed answers can be supplied in the call. Named decisions, saved definitions, the Playground, and reusable helpers are optional. There is no mandatory decision registration, project creation, dashboard, account, or industry template.

The end user stays in the application's normal flow. The application uses existing context and asks for genuinely missing information in place. It never asks the user to fill in Decilist configuration, inspect JSON, select a model, or understand confidence thresholds.

## Historical starting point

| Capability | Observed state on October 25 |
| --- | --- |
| Repository and foundation | Public initial commit `5dd3bab`; 31 Python and 8 TypeScript tests passed; GitHub CI passed |
| Direct SDK calls | Implemented; Python runtime and HTTP client, TypeScript HTTP client, shared validation |
| Policies and local demo | Implemented with deterministic rules, fallback, abstention, and a technical Playground |
| Liquid d1-3B | Adapter and mapping tests exist; weights not downloaded; no live model inference |
| Easy local installation | Pending; TypeScript currently requires a separately started Python runtime |
| User experience and model quality | Target flows and measured acceptance still pending |
| Published packages and OpenAI Decisions | Not implemented/published |

Passing the foundation tests does not establish learned decision quality or integration usability. The current demo's deterministic probabilities are not model confidence estimates.

## Implementation progress — October 25

- Implemented explicit runtime setup/start/run/status, compatibility/readiness checks, once-configured client policies, and owned-server shutdown. Fresh wheel/tarball applications outside the monorepo have exercised all three operations against the baseline.
- Implemented the packaged optional one-workspace Playground, local correction/test loop, and copied SDK integration. Implemented a separate normal-app request composer with one clarification and no exposed model settings.
- Added and ran a frozen 60-case text dataset, three repetitions each. The [measured model report](model-validation.md) records 60% held-out coverage, 66.7% accepted classification accuracy, score MAE 0.985, and concrete failures. This is limited task quality, not production readiness.
- Installed and verified the pinned Liquid snapshot in an isolated environment. Cached local inference succeeds on Windows RTX 3090, including a second primary-agent run with Python socket connections blocked. This bounded check does not establish OS-level network isolation.
- Both installed SDK artifacts executed all three operations against the real Liquid HTTP runtime from applications outside the repository. Windows/Ubuntu CI passed. Human first-use timing, Linux GPU compatibility, and the November 1 presentation rehearsal remain outstanding.

The dated acceptance gates below remain the completion criteria. A working baseline or package does not tick a gate that requires a real model run, human usability observation, or later presentation rehearsal.

## Required product behavior

1. Direct calls work without saved definitions. Preserve the existing public calls while simplifying setup and examples.
2. Provider setup happens once per application, not per decision. Input, question, and choices remain explicit; no hidden guess about the developer's intent.
3. Python can use the model in process. TypeScript connects to a managed local runtime or a configured endpoint; other languages use HTTP. Do not imply native TypeScript inference exists.
4. Local setup is explicit and resumable. Show download size/progress, model terms, compatibility, and errors. No model download, process startup, or paid/cloud request merely from importing the SDK.
5. Target one local setup/start command, with no manually coordinated Python terminal for routine TypeScript use. Runtime discovery, readiness checks, version compatibility, reuse, and shutdown belong to that packaging work.
6. Defaults are documented and conservative; uncertainty returns a distinct status. A threshold is not a guarantee of correctness. An unavailable model must not silently become a rules engine or a remote provider.
7. Advanced controls are available without being prerequisites for a first call. Application code owns what happens after the decision.
8. Core execution has no dependence on a dashboard, account, telemetry, or external network after local dependencies and model assets are cached.

## Work sequence and acceptance

### 1. Prove the local model path — October 26

Prepare an isolated CUDA-compatible environment, inspect and pin vendor code/dependencies, download the pinned d1-3B snapshot, and run all three operations on the target machine. Keep the model environment separate from the foundation environment so a failed setup does not break the baseline demo.

Record setup time, download size, exact versions, GPU memory, cold start, warm timings, and failures. Verify a second run without internet and capture evidence of outbound network behavior; an environment flag alone is insufficient proof.

**Done when:** choose, predict, and score return valid model-derived results; the source model and revision are recorded; the offline run succeeds. This validates the integration, not overall model quality. If the runtime is incompatible, record the blocker and investigate a supported runtime without silently substituting models.

### 2. Make SDK integration direct — October 27

Keep the current direct-call primitives. Add a clear single provider-connection path, useful errors, small Python and TypeScript quickstarts, and installable local wheel/tarball artifacts. Test those artifacts from a clean project rather than relying on monorepo imports. Public registry publication is a separate release action, not a prerequisite for this milestone.

Build the smallest managed-runtime setup needed for the presentation machine. Use one explicit launcher that checks dependencies, starts or reuses the correct runtime, waits for health, and reports an actionable failure. It must not take over unrelated processes. Document supported operating systems rather than claiming cross-platform support from one machine.

**Done when:** both SDKs execute a real local decision from fresh applications without using the Playground or registering a decision. Target at most 10 application-code lines for connection, one call, and status handling. With runtime and weights cached, target a first useful decision within 10 minutes; measure total cold setup and download time separately. These are usability targets, not current results.

### 3. Establish model quality and failure behavior — October 27–28

Create at least 60 small, authored text cases: 20 development cases for tuning and 40 held-out cases for final evaluation. Include all three operations, paraphrases, missing context, ambiguity, unexpected input, and instructions embedded in evidence. Keep the existing 12-case rules suite as a wiring check, separate from this dataset.

Use classification/predicate accuracy, coverage, abstention, and numeric score error. Record warm p50/p95 over a stated repeat count, cold load, timeouts, memory, and unmeasured cost as unknown. Select any policy thresholds on development cases and freeze them before evaluating the held-out split. Compare local rules and model behavior only where both address the same task.

**Done when:** a reproducible report shows actual results and failure cases, not only successful screenshots. Low measured quality narrows the demonstrated use case; it does not justify hiding abstentions or changing held-out labels to improve the report. General model superiority and production-readiness claims remain out of scope.

### 4. Simplify the optional Playground — October 28–29

Replace the settings-heavy entry experience with one workspace: question, example input, and result. Show choices or a score rubric only when the chosen operation needs them. Keep provider settings, policy thresholds, raw JSON, and traces behind an advanced area. Do not remove access to them.

Allow the developer to correct an answer and retain it as a local test case, rerun those cases after a change, and copy an SDK example that reproduces the current configuration. Saving a named decision remains optional; a developer using only code has the same capabilities.

**Done when:** question → result → optional correction/test → copy integration all work in the same workspace. No separate wizard or project-creation screen is needed. Verify empty, loading, failure, abstention, and success states; inspect desktop and narrow layouts, keyboard access, and labels.

### 5. Demonstrate integration inside a normal app — October 29–30

Build one small, domain-neutral example around an existing request composer. A user enters a request once. The app uses available context, proceeds when sufficient, asks one focused clarification when a necessary field is missing, or explains that review is needed when the decision is uncertain. The decision SDK does not generate clarification prose; use application-authored wording unless a separate generation feature is explicitly introduced.

The app owns the action mapping. Never auto-execute a consequential action just because the model selected it. Model/runtime failures must produce an intelligible in-place state, not a broken form or an invented successful result.

**Done when:** the end user completes the example without seeing Decilist settings or re-entering known data. Show the ordinary app first in the presentation, then the short integration code and evaluation evidence.

### 6. Verify first-use usability and freeze — October 30–31

Repeat setup from a fresh project on the presentation machine using only the public instructions and built package artifacts. Record steps, elapsed times, manual interventions, and confusing errors. A scripted clean-install run proves reproducibility; it is not a substitute for a human usability observation. If a first-time developer walkthrough is available, record its results separately.

Run contract, policy, packaging, HTTP, real-model, and browser checks appropriate to the final changes. Freeze the demo candidate on October 31, with reproducible assets, known limitations, a short recording, and a clearly labeled baseline fallback. Rehearse a five-minute presentation without internet after setup.

**Done when:** the exact candidate has passing checks, honest measured results, a working offline presentation path, and concise recovery instructions. If live model inference still fails, present the work as a foundation prototype rather than a completed local-model product.

## Calendar and priority

| Date | Primary outcome | Dependency |
| --- | --- | --- |
| Mon Oct 26 | Verified d1-3B loading and offline operation | Compatible isolated environment and pinned snapshot |
| Tue Oct 27 | Direct SDK quickstarts and initial runtime launcher | Verified local provider; dataset authoring can proceed independently |
| Wed Oct 28 | Real evaluation report and simplified Playground | Stable model contract and held-out cases |
| Thu Oct 29 | Normal-app integration and installable package checks | Direct SDK path and usable uncertainty handling |
| Fri Oct 30 | Fresh-project walkthrough and full rehearsal | SDK, app, and evidence connected end to end |
| Sat Oct 31 | Freeze, fixes, offline rehearsal, backup | Final candidate passes its acceptance gates |
| Sun Nov 1 | Presentation | Demonstrate only verified behavior |

The critical path is local inference → direct SDK installation → normal-app integration → verification. Dataset work and Playground work may overlap this sequence without making a model-success claim early.

## Scope cuts if time is tight

Protect real local inference, direct calls, understandable failures, one integrated app example, and honest evaluation. Reduce Playground styling, saved-case conveniences, or comparison features before weakening those essentials. If one-command runtime management is not ready, document the explicit launcher and remaining manual steps instead of claiming zero setup.

OpenAI Decisions is optional after the local experience passes. Its integration requires actual account access, contract checks, explicit remote opt-in, and measured usage. A rules/model comparison does not establish interoperability between two learned-model vendors.

A mandatory decision registry, prescribed application workflow, and required industry templates are excluded from the product contract, not postponed requirements.

Defer mobile applications, multimodal API, fine-tuning, hosted dashboards, multi-tenant storage, native TS model inference, high-throughput batching, hard GPU cancellation, automatic calibration, and broad benchmark leaderboards. Production remote hosting/authentication is a separate scope. Do not add these to the first-use journey.

## Definition of presentation readiness

- [x] A new project can make direct choose/predict/score calls without registering decisions or opening a UI.
- [x] Runtime setup is documented and scripted launcher behavior verified; remaining manual requirements and supported systems are explicit.
- [x] Liquid executes locally with recorded versions and real outputs; offline verification is bounded to cached loading and blocked Python socket connects.
- [x] Invalid, uncertain, timed-out, or unavailable results lead to inspectable outcomes that the app handles.
- [x] A held-out report distinguishes model quality, coverage, latency, and unmeasured costs.
- [x] The end-user example stays in its own interface and reuses existing context.
- [x] The optional Playground has one primary workspace and reproduces its result in copied SDK code.
- [ ] The final candidate, packaging, tests, browser flows, and rehearsal are verified.

Completed gates refer to the measured alpha scope. Current model errors remain visible; no production-readiness claim follows. First-time human usability, a final candidate freeze, recording, and full offline presentation rehearsal still need to happen before November 1. Any future prompt/model improvements need development-only tuning followed by fresh held-out evaluation; the existing report and labels must remain intact.
