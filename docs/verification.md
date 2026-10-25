# Foundation verification — October 25, 2026

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

These results do not verify learned inference, CUDA compatibility, model quality, model calibration, cost per decision, production hosting, or mobile rendering. The first local model acceptance run and optional OpenAI integration are explicitly pending. GitHub CI is separately observable on the repository's Actions page; a workflow file alone is not a passing CI result.
