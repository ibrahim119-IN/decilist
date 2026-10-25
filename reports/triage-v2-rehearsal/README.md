# Live browser rehearsal — October 26, Cairo

This was an **agent-assisted live walkthrough**, not a first-time human observation or an offline presentation rehearsal. The local Liquid runtime was already running. No restart, model download, external action, or network-setting change occurred.

The observed interval was 00:24:14–00:27:00 Cairo (165.899 seconds, including agent/tool interaction). Timing starts at the first normal-app submit; it excludes setup. The [structured record](browser-walkthrough.json) contains every scoped check and limitation.

## Observed outcome

- Normal app: clear request produced a local draft preview; missing format asked one focused question; Arabic continuation retained the original request and source; ambiguous format showed review.
- Playground: real local model returned `retry` at 96.6% for the transient-failure example. The UI displayed 240.3 ms. At a temporary 100% threshold, the same model flow visibly abstained with `No answer` and 287.7 ms. The normal 70% threshold was restored.
- Developer loop: an owned expected-`retry` case passed. A separately named, deliberately corrected-to-`review` expectation failed with `got retry`, demonstrating the mismatch path. Both disposable cases were removed and the count returned to zero. This deliberate mismatch is not model-quality evidence.
- Python and TypeScript examples matched the current question, state, allowed answers, endpoint and policy. The copy button reported `Copied`, but the documented browser clipboard read returned empty; actual clipboard bytes were not established here. Installed SDK execution belongs to the primary agent's separate checks.
- One allowed answer produced a clear validation message; the original choices were restored. Empty, loading, accepted, validation-error and abstention states were observed.
- Screenshots at the normal desktop size and 390×844 were visually inspected. Both pages stacked cleanly with no observed horizontal clipping; Arabic continuation and the abstention card remained readable. The temporary viewport was reset. This is browser viewport QA, not physical mobile-device testing or a complete accessibility audit.

CUA displayed actual screenshots, but no documented screenshot-file export operation was available. This folder intentionally contains no image replicas or invented captures. Existing images elsewhere in `reports/ui/` belong to earlier checks and must keep their original provenance.

## Offline evidence and fallback boundary

The existing [cached smoke record](../liquid-smoke-2026-10-25.json) records successful local choose/predict/score calls, hub offline mode, a Python socket `connect`/`connect_ex` blocker and zero attempts within that instrumentation. It does not establish process-level packet capture, an OS air gap or a full offline browser presentation. The [setup record](../model-setup-2026-10-25.json) verifies the pinned cached snapshot and records its approximately 6.27 GB size. The observed overlapping setup interval was about 46 minutes 57 seconds and excluded earlier environment creation.

Advanced UI confirmed one `liquid-local` provider, `LiquidAI/d1-3B` revision `051bcc464b01b9f92942b364d9586b0ef5912432`. There is no live provider-toggle control. No baseline substitution was performed. A fallback should use a separately, explicitly started `demo` rules runtime on another free port, labelled as a deterministic wiring demonstration. Its current instructions are in [runtime.md](../../docs/runtime.md). The existing Liquid server must remain running.

## Presentation boundaries

The [Arabic five-minute script](../../docs/presentation-rehearsal-ar.md) leads with the ordinary app, then direct SDK integration, existing-project triage and measured failures. At the time of this walkthrough, triage v2 was still being implemented. Its slot remains explicitly conditional until the primary agent verifies actual CLI output and the final package. No human debugging-time savings, calibrated confidence, cross-platform GPU support, production readiness, or OS-level network isolation can be claimed from this check.

Final browser state: `/demo`, clear request, `DRAFT READY`. The Liquid runtime was left running.

## Follow-up — removal notice fixed and fallback executed

The first walkthrough also found that deleting saved cases could leave the prior run's pass-count sentence visible. The primary agent explicitly scoped this small correction into the task. Only `packages/python/src/decilist/static/app.js` was changed: a successful deletion drops the removed case's cached result and announces its removal. A failed local-storage write retains the cases and its error message.

The resumed actual browser check used Edge because the earlier IAB session was unavailable. At 21:29:37–21:29:51 UTC, one uniquely named disposable case returned `retry`, was saved, and reran as `1 of 1 local tests passed`. Removing it then showed `Local tests 0`, `No tests yet`, disabled `Run tests`, and a fresh removal notice. The old pass-count sentence was absent. The desktop screenshot was visually inspected. The owned case was removed; no unrelated cases were changed. `node --check` passed. No mirrored implementation-only test was added for this small UI correction.

A separate explicit `demo` runtime was then started on previously free port **8788** in an owned terminal session. Live UI verification showed `RULES WALKTHROUGH · LOCAL`, the limitation text, `retry` with the `Rules baseline` label, and the disclosed `uniform low confidence → workflow-rules accepted` trace. Malformed JSON visibly abstained and retained the rules-error attempt. The normal app also showed `Rules walkthrough · local` and prepared its clear local preview. This supersedes the earlier “not freshly executed” status; those earlier observations remain preserved.

Only that owned fallback server was stopped. Its shutdown completed for PID24436, port8788 stopped listening, and the original Liquid runtime remained on port8765/PID36324 with `ready=true`. The browser returned to the live Liquid `/demo` and `DRAFT READY`.

The newer [primary-agent cached model rehearsal](../triage-v2-offline-rehearsal-2026-10-26.json) was also inspected. It records accepted choose/predict/score calls on `cuda:0`, a cached model load of 23.657 seconds and zero attempted connections while Python socket connections **and DNS** were denied during model loading and inference. This is a successful bounded cached-model rehearsal, separate from the browser run. It still does not establish OS-level isolation, native-code traffic observation or a full offline browser presentation. No network setting was changed.

## Actual terminal recording and replay

The subsequent [self-contained HTML replay](terminal.html) and [timed JSON record](terminal.json) retain six real subprocess commands against the installed clean-consumer wheel, SHA-256 `4cac6713558857b8060162c96e1b81946d20828898db3836d3cff62f6592818b`. Total observed command time was **2.244 seconds**, not repair time. No fixture source was changed.

The separate tiny authored project produced actual `expected 5 but got 4` and missing-module tracebacks. The installed v2 CLI returned `needs_review / source: model / exit 1` for the assertion and `suggested / source: rules / exit 0` for the missing dependency. A direct installed Python `Client.choose` call returned accepted `summarize` from `liquid-local` / `LiquidAI/d1-3B`. All actual output and exit codes remain present. Command prompts and exit timing annotations are explicitly identified as recorder metadata. Personal/project/executable paths were replaced with documented aliases; no original personal path was retained in the deliverable.

The replay has play, pause, restart, 0.25×–2× speed and seeking. Actual Edge visual QA covered the normal desktop and 390×844 viewport, including playback completion and the final exit annotation. The captured JSON equals the data embedded in the HTML; event ordering, path-redaction and no-external-asset checks passed; inline JavaScript syntax passed. The owned preview server on8789 was stopped after QA, and the original Liquid8765/PID36324 remained listening. No video, human walkthrough, browser offline or OS air-gap claim follows.

The [recording script](../../scripts/record_triage_rehearsal.py) requires an explicitly selected installed Python, wheel, and separate authored project containing `check_total.py` and `missing_dependency.py`; it refuses to overwrite existing recordings. Reserved repair-trial cases were not used for the recording. The Arabic presentation script now describes verified v2 CLI outcomes and the fresh eight-case evaluation; earlier planned-status observations above remain historical.
