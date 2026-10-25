# Optional Playground and ordinary-app example

The installable runtime and repository API serve the same packaged interface. `/` is the optional developer Playground; `/demo` is a small existing-app request composer. Neither is required by a direct SDK call.

## Playground

One workspace contains a question, example input, result, local corrections/tests, and an integration example. Choose shows allowed answers, Predict needs no answer configuration, and Score shows only its ordered rubric. Provider identity, confidence/time-limit controls, response JSON, and policy attempts remain available in collapsed advanced areas.

In rules walkthrough mode the workflow question and answer definition are fixed and read-only. The UI explicitly states that these are authored rules and a uniform baseline, not a demonstration of model intelligence. Model mode unlocks custom questions and definitions. The runtime decides its provider once; a failed model never silently becomes rules in this interface.

Saving a correction retains the full question, input, policy, name, and expected answer in this browser's local storage. No account, project, saved decision definition, or network storage is involved. Reruns report pass/fail/error for every retained case. Score expectations use a displayed absolute tolerance of 0.05. The 100-case cap limits browser storage growth; removal is local. These cases are developer-authored tests, not held-out evaluation evidence.

One compact language selector switches the generated example between Python and TypeScript, with one copy button. Each example invokes the current operation with exactly the visible question/input/answers and current policy at the same runtime URL. Python uses `Client`; TypeScript connects once with `Decilist.connect({baseUrl, policy})`. Both handle abstention. The copy action has a text-selection fallback when clipboard access is unavailable.

## Request desk

This distinct app example prepares a **local request preview**, not generated document content. The application already knows its current document (`Project update`) and destination (`Private drafts`). The user enters one natural request; those known fields are supplied by the app and never re-entered.

`POST /v1/demo/request` calls `engine.choose` on that combined context using the same configured engine as `/v1/decisions`. The decision selects `proceed`, `clarify`, or `review`. The app owns the output mapping, clarification wording, and local preview. An accepted clarification shows one app-authored question about the required draft format. A second unclear answer goes to review, preventing repeated clarification loops. Abstention leads to review; provider error/timeout/invalid output leads to a retry message. Input validation and HTTP failures retain the original request.

The example supports summaries, outlines, and checklists. Its deterministic baseline recognizes a small documented family of English and Arabic phrases, so arbitrary language understanding is not claimed. Text entry and preview use automatic text direction; an Arabic request retains its original text, known source, and destination. Consequential requests cannot execute an external action: the only proceed behavior is a local preview, and the app also conservatively reviews common send/publish/delete wording regardless of the selected result.

This app task is **separate from the request-text benchmark** (summarize/proofread/shorten with source/destination completeness). That benchmark does not measure this app's draft-format routing. There has been no tuning against its held-out cases. Real local-model outcomes and their limits must be verified separately before presenting model quality.

## Observed verification — October 25, 2026

- 21 targeted API/app tests passed, including baseline proceed/clarify/review, Arabic, original context reuse, one-clarification limit, app-owned consequential-action refusal, abstention, invalid output, input limits, packaged assets, and existing local HTTP boundaries.
- Both shipped JavaScript modules passed Node syntax checks.
- Desktop browser: Choose returned `retry`, Predict `False`, Score `1.00 / 2`; malformed workflow input visibly abstained. A saved local case reran as 1/1 passed; the SDK copy button reported success and its displayed source was inspected. Completed outcomes focus the result heading and bring the answer into view on desktop and narrow layouts.
- The exact DOM-generated Python SDK example was executed and returned `retry` against the same runtime. Actual DOM-generated TypeScript snippets for Choose/Predict/Score and an abstention were checked by the TypeScript compiler against the workspace SDK, then executed unchanged against the same runtime: `retry`, `false`, `1`, and `Needs review`. The inputs included quotes, backticks, interpolation-looking text, Arabic, and a newline to verify string escaping. The compact language selector and copy button were also visually inspected at 390 px.
- The shipped TypeScript generator preserves the valid answer labels `__proto__`, `constructor`, and `other`. Its Node regression executes the generated code and checks all own keys and their meanings. Only `__proto__` uses computed-property syntax; ordinary labels keep normal object literals. The generated snippet also compiled with an assertion that accepted values retain the exact three-label TypeScript union. Executing that generated call through the actual workspace SDK with a custom transport preserved all three question/probability labels and accepted `__proto__`.
- Request desk browser: clear request produced a local preview, missing detail produced the focused question, keyboard Enter completed clarification, uncertainty produced review, and an Arabic request displayed with right-to-left direction and preserved known source.
- Actual CSS viewports of 390 px were visually inspected for both pages through a temporary local iframe harness. The harness alone relaxed frame embedding for this check; the shipped runtime retains `frame-ancestors 'none'`. No horizontal clipping or overlapping controls was observed. This is viewport QA, not a claim of tested physical mobile devices.
- A temporary test harness delayed a request by three seconds to inspect pending/disabled states and returned HTTP 503 to inspect the explicit failure state. This harness lives outside the package and does not affect product execution.

Screenshots are in `reports/ui/`: `playground-desktop.png`, `playground-narrow.png`, `playground-narrow-result.png`, `playground-narrow-sdk.png`, `playground-pending.png`, `playground-failure.png`, `demo-desktop.png`, `demo-clarification.png`, `demo-narrow.png`, and `demo-arabic.png`.

The browser checks above use the explicitly labelled rules baseline. They establish integration and interface behavior, not learned-model quality, user-study usability, model calibration, or production readiness.

The primary agent also ran 12 broader, authored app-flow checks through the live HTTP endpoint using `scripts/verify_app_model.py --provider demo`. The baseline matched **11/12** expected routes. It asked for clarification for an unsupported translation request instead of routing to review. This is a disclosed limitation of the small keyword baseline, not a successful learned-model result. The [preserved report](../reports/app-baseline-2026-10-25.json) records every expected/actual route and underlying decision. The same unchanged cases can be run against Liquid; they are separate from the 60-case frozen text benchmark.
