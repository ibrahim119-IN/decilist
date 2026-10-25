# Verification — October 25, 2026

## Integration increment

The final application-guard increment passed **147 Python tests** in a primary-agent full-suite run. The rebuilt wheel was installed outside the repository and served the same 12 app-flow cases successfully. The real Liquid server also matched **12/12 final application routes**. Its [initial 10/12 report](../reports/app-liquid-before-guards-2026-10-25.json) and [guarded 12/12 report](../reports/app-liquid-guarded-2026-10-25.json) preserve the same case hash and actual model decisions. These are regression checks on known cases, not a new held-out model result. The guards decline explicit translation, ambiguity, multiple formats, and consequential requests using limited English/Arabic lexical checks; they are not an exhaustive semantic boundary.

The real-model `runtime run` path was also exercised from a stopped runtime: it loaded the cached model, ran an installed Python SDK application, returned exit code zero, shut down, and left no listening port. The TypeScript launcher successfully reused the running Liquid service. The primary agent then completed a live browser clarification with an Arabic answer; the original request and known source remained visible. Screenshots: [normal app](../reports/ui/demo-liquid.png) and [custom Playground question](../reports/ui/playground-liquid.png).

The primary agent reran the Python suite after the Liquid compatibility fix: **119 passed**, including 13 focused compatibility cases. Earlier integration verification also passed **21 TypeScript SDK tests**, **one generated-code test**, strict type checks, and **10 actual Python decision envelopes** validated by TypeScript. This includes numeric and prototype-like choice labels. The updated Python wheel/sdist built and installed successfully outside the repository. Counts below refer to the historical foundation.

Fresh wheel and npm tarball installs were exercised from new application directories outside the repository. Both HTTP SDKs completed choose, predict, and score. The TypeScript application fetched all five packaged UI routes/assets successfully. The explicit launcher waited for readiness, forwarded application arguments, and stopped its owned server. These measured runs use the rules baseline; they prove packaging, contracts, and lifecycle, not learned decision quality or a first-time human usability study.

The primary agent also inspected the desktop interface and 390px viewport captures, and personally completed the normal-app clarification using an Arabic answer and keyboard Enter. The original request and known document context were preserved. [UI evidence](ui-experience.md) describes the controlled pending/failure checks, saved test rerun, SDK snippet execution, and screenshots.

The [new evaluation suite](evaluation-v2.md) froze 20 development and 40 held-out authored English cases before the learned-model run. The [model report](model-validation.md) now records all 180 calls, actual errors, coverage and timings. The held-out accepted classification accuracy is 66.7%; valid outputs and fast execution do not establish correct decisions.

The primary agent independently reran cached Liquid inference on `cuda:1`: all three operations returned valid model-derived results, three repetitions each. Python socket connection attempts were blocked and none occurred; this is not OS-level network isolation. Both the fresh installed Python wheel and TypeScript tarball then called the live Liquid HTTP runtime, returning `summarize`, `true`, and score `1.9384756068981595` under the normal 0.7 confidence policy. No Playground or registered decision was used. The actual model/revision were verified through runtime health before sending these examples.

[Observed CI](https://github.com/ibrahim119-IN/decilist/actions/runs/40228553717) passed both Windows and Ubuntu for implementation commit `65e3cc1`. GPU model execution was measured only on Windows; CI uses the baseline and lightweight compatibility tests.

## Historical foundation

Observed on Windows with Python 3.12 and Node.js 22.23.1:

- Python: 31 passing tests covering contracts, invalid probabilities, abstention, fallback, timeouts, caller cancellation, locality enforcement, vendor mapping, native-worker serialization, evaluation semantics, request validation, host/origin boundaries, body limits, and API assets.
- TypeScript: 8 passing tests plus successful strict compilation and positive/negative type checks. Shared fixtures cover choice, predicate, score, and tie behavior.
- Python example: all three decision primitives ran successfully against local baselines.
- TypeScript example: executed against the running Python HTTP API and selected `retry` after the uninformative provider failed the confidence policy.
- Python HTTP client: choose, predict, and score executed against the running API with semantic response validation.
- Browser: choose selected `retry`, predict returned false, score returned 1.00, and malformed JSON led to explicit abstention. Desktop rendering inspected; saved screenshot in `docs/playground.png`.
- Python sdist/wheel build and npm package dry run succeeded. Both package layouts include the project license.
- npm audit reported no known vulnerabilities. The Python environment audit initially identified old installer/server dependencies; these were updated, followed by a clean audit and rerun of API tests. Optional model dependencies were not installed or audited.
- Dependency constraints and OpenAPI schema saved. Git diff whitespace and scoped credential-pattern checks performed before publishing.

The historical foundation results did not verify learned inference, CUDA compatibility, model quality, model calibration, cost per decision, production hosting, or mobile rendering. Optional OpenAI integration remains pending. GitHub CI is separately observable on the repository's Actions page; a workflow file alone is not a passing CI result.
