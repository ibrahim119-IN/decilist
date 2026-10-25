# Architecture and contracts

## MVP boundary

The Python package is the decision runtime. TypeScript and Python HTTP clients share a public contract with all other languages. This avoids maintaining two model runtimes before the November 1 demo. Custom Python providers implement `name`, `model`, `locality`, and `async infer(question) -> probabilities`. A TypeScript `Transport` changes where requests execute; it is not itself a model provider.

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
- `choose`: 2–64 distinct named options. The highest probability wins. Ties use declared option order, not provider response order.
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
