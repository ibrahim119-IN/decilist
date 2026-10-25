# Triage verification record — October 26, 2026

Checks below were executed on the Windows development machine. Machine measurements use the actual resident Liquid runtime; ordinary software tests do not download or require model weights.

| Check | Observed outcome |
| --- | --- |
| Full Python suite after final triage/lab/study integration | 272 passed in 37.79 seconds |
| TypeScript SDK tests and generated snippet check | 21 + 1 passed via `npm test` |
| Actual Python output envelopes validated by TypeScript | 10 accepted via `scripts/verify_contract_parity.py` |
| Python source distribution and wheel build | Both built with `python -m build packages/python` |
| Clean consumer environment | Built wheel installed with its core dependencies; installed evidence helper bytes matched frozen source |
| Installed-package CLI outside source checkout | Suggested `dependency_environment` on an actually failing isolated missing-import fixture; local Liquid provider and three source references retained |
| Frozen-source verification | Every Git-staged evaluation source SHA-256 matched `triage_freeze.json` before the source commit/model run |
| Fixture proof | 10/10 expected baseline/mutation/reference-repair sequences verified; no mutation to the live source tree |
| Identical machine inputs | Provider/case/repetition evidence hashes matched across baseline and Liquid |
| Human study preparation | Two failing cases prepared; both `started_at` and human durations unset, zero hint requests |

An initial attempt to build without isolation failed because the test environment does not contain setuptools. The normal isolated build succeeded without modifying that environment. No required check was weakened.

The built wheel used for the consumer check has SHA-256 `fdee1a10e96a63e25b64e367eef72d62a95df0b5141ad0e3ad958a4ba94dbb21`. Build output stays local and is not a public package release.

Retained evidence hashes (UTF-8, LF):

- `reports/triage-machine-2026-10-26.json`: `008133da471cc3799b94a59528f249e1b349c883d1c72802f123049957cda2b2`
- `reports/triage-fixture-proof-2026-10-26.json`: `de094a59020c4450c84ea20b67b2bd8d7183ad720d5f5b326e5d0332d5d9e554`
- `reports/triage-installed-example-2026-10-26.json`: `b28c2ed42d15fd1e7fbcb6776a9ebc3c10e2e614d59fe68a72b9ee466da02a3d`

These checks establish implementation behavior, executable fixtures, package integration, and faithful recording of the comparison. They do not establish model usefulness, human debugging-time savings, broad log-format support, complete secret detection, or production readiness. The [observed model result](triage-results.md) remains negative for added value on this small corpus.
