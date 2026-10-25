# decilist — Python

Local-first decision contracts, provider policies, and evaluation.

This alpha includes direct choose/predict/score calls, an async HTTP client, an explicit local runtime launcher, and an optional packaged Playground. Liquid inference dependencies are separate. Real local d1-3B inference was verified on Windows/RTX 3090, with substantial task-quality limitations recorded in the [measured results](https://github.com/ibrahim119-IN/decilist/blob/main/docs/model-validation.md). The default demo is explicitly labelled deterministic rules.

An optional saved-log helper, `python -m decilist.triage --log failure.log --project .`, proposes a bounded next check for failed tests using a learned local runtime. It never runs repairs. See the [triage walkthrough](https://github.com/ibrahim119-IN/decilist/blob/main/docs/triage.md); debugging-time benefit is not yet established.

See the [repository README](https://github.com/ibrahim119-IN/decilist#readme) for setup, examples, API contracts, and limitations.
