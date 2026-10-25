# Verification — October 25, 2026

## Integration increment

The primary agent reran the full current Python suite: **106 passed**, plus **21 TypeScript SDK tests**, **one generated-code test**, and strict type checks. All **10 actual Python decision envelopes** also passed TypeScript validation. This includes numeric and prototype-like choice labels, preserving the public contract across languages. Python wheel/sdist builds passed. Counts below refer to the historical foundation.

Fresh wheel and npm tarball installs were exercised from new application directories outside the repository. Both HTTP SDKs completed choose, predict, and score. The TypeScript application fetched all five packaged UI routes/assets successfully. The explicit launcher waited for readiness, forwarded application arguments, and stopped its owned server. These measured runs use the rules baseline; they prove packaging, contracts, and lifecycle, not learned decision quality or a first-time human usability study.

The primary agent also inspected the desktop interface and 390px viewport captures, and personally completed the normal-app clarification using an Arabic answer and keyboard Enter. The original request and known document context were preserved. [UI evidence](ui-experience.md) describes the controlled pending/failure checks, saved test rerun, SDK snippet execution, and screenshots.

The [new evaluation suite](evaluation-v2.md) freezes 20 development and 40 held-out authored English cases before a learned-model run. Its measured uniform reference is not a model result. Hardware/model acceptance remains pending until a separate observed report is recorded.

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
