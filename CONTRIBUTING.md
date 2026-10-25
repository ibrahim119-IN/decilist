# Contributing

Start with the README's local setup. Keep model downloads and external API requests out of the default test suite. Do not commit credentials, private data, model weights, environment folders, or generated caches.

Run `python -m pytest -q` and `npm test` from the root. When changing a public decision contract, update both clients, the shared contract cases, generated OpenAPI, and documentation. Model adapters need recorded vendor-schema evidence, mapping tests, and separately identified live-inference evidence. Do not mark fixtures as live model results.

Contributions are licensed under Apache-2.0. Keep unrelated changes out of a pull request. Explain the behavior change, checks run, and limitations. Open an issue for breaking contract changes or new dependencies before a substantial implementation.
