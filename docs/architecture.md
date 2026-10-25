# Architecture and contracts

## MVP boundary

The Python package is the decision runtime. TypeScript and Python HTTP clients share a public contract with all other languages. This avoids maintaining two model runtimes before the November 1 demo. Custom Python providers implement `name`, `model`, `locality`, and `async infer(question) -> probabilities`. A TypeScript `Transport` changes where requests execute; it is not itself a model provider.

## Integration experience

The primary integration is a direct choose/predict/score call with input, a question, and any required choices or rubric. Provider configuration happens once. Named decisions and reusable definitions are optional helpers, not a mandatory registration layer. The SDK does not require a Playground session, project, dashboard, account, workflow framework, or changes to the application's interface.

The application owns actions and end-user presentation. It can use a decision to proceed, ask for missing information, or request review in its existing flow. Decilist does not generate clarification prose or build that interface automatically.

The explicit `decilist runtime run --provider ... -- node app.mjs` launcher loads or reuses a compatible Python runtime, waits for readiness, runs the application, and shuts down only the server it owns. A shared foreground runtime is also available. Python and TypeScript clients can connect once and share their policy across calls. No model downloads or process creation happen merely on SDK import. Local inference may run on the developer's machine or the application's private server; end users do not inherently need to install a model. See [runtime details](runtime.md).

```text
Python in-process / Python HTTP / TypeScript / other HTTP caller
                              |
                     validated question
                              |
          server local-only floor + request policy
                              |
           ordered providers -> distribution validation
                              |
              accept / fallback / explicit abstention
                              |
                 result + attempt trace -> evaluation
```

## Semantics

- Version 1 accepts a nonempty text state; JSON can be encoded into that text. Native JSON, images, audio, batching, and streaming are deferred.
- `choose`: 2–64 distinct named options. The highest probability wins. Ties use the canonical question-key order, not provider response order. To agree with JavaScript JSON objects, canonical ASCII array-index keys (`0` through `4294967294`, without leading zeros) come first numerically; other keys retain declaration order. Python normalizes to that order before inference. Labels such as `__proto__` and `constructor` remain ordinary own data keys, not object configuration.
- `predict`: P(true) and P(false). The boolean threshold is 0.5. Policy confidence is the probability of the selected boolean, not always P(true).
- `score`: 2–10 distinct level descriptions, ordered low to high. Output is `sum(index * probability)`, not a rounded category or a free-form number. Confidence is the largest level probability; this is deliberately different from proximity of the expected score to a level.
- Providers must return exactly the requested labels and finite probabilities in [0,1] summing to one within 1e-5. Invalid distributions are rejected, not silently repaired.
- `accepted` has a value and provider identity. `abstained` has no value or probabilities; callers must handle it explicitly. No automatic real-world action is performed.
- Policy defaults: local-only, minimum confidence 0.7, timeout 5000 ms **per provider attempt**. This is not a total request deadline. Confidence thresholds are not calibrated guarantees.
- Fallback happens for low confidence, malformed output, timeout, and provider errors. Caller cancellation propagates. Trace entries omit raw errors and evidence to reduce accidental disclosure.
- Cost is unmeasured (`null`), including for local inference. Local operation is not necessarily free.

## Execution and boundaries

The API enforces local-only even if a request asks to disable it. The MVP exposes no provider-selection or endpoint-configuration field to untrusted requests. A provider's locality is a trusted plugin declaration; the SDK is not an operating-system network firewall.

The development server binds to loopback, restricts Host and browser Origin, limits request bodies, and serves fixed local files. It is not a production service. Remote hosting needs authentication, TLS, quotas, a total deadline, deployment review, and an explicit privacy policy. Configuring a remote HTTP runtime sends input to that runtime even if model inference there is local.

Fast local rule functions execute directly and must not block the event loop. Native Liquid inference uses a worker thread and a lock. A timeout cannot terminate a GPU kernel; it stops waiting while the running job retains the lock. Later requests cannot overlap that native inference. Hard cancellation and process isolation are later work.

## Differentiation to validate

Vendor SDKs already offer typed decisions, and Liquid's model already supplies probabilities. Decilist's contribution is the portable contract, policy boundary, observable fallback behavior, local development experience, and evaluation loop. The MVP does not claim these primitives are novel on their own.

Acceptance evidence should show: integration with a second provider without changing application decision code; fewer model-specific checks in that code; a workload-specific quality/latency/coverage comparison; and a reproducible local run. Until then, this is an implementation hypothesis, not a proven business advantage.
