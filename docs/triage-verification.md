# Triage verification record — October 26, 2026

Checks below were executed on the Windows development machine. Machine measurements use the actual resident Liquid runtime; ordinary software tests do not download or require model weights.

| Check | Observed outcome |
| --- | --- |
| Full Python suite after the Ubuntu URI portability repair | 276 passed in 31.71 seconds on Windows |
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

The first built wheel used for the consumer check had SHA-256 `fdee1a10e96a63e25b64e367eef72d62a95df0b5141ad0e3ad958a4ba94dbb21`. After the URI portability repair, the wheel was rebuilt and reinstalled in the separate consumer environment: SHA-256 `9efa07727c70991a09f443d403abcd730e3aeb00d1d4c0f8fcdffce6d3063e7b`. Its installed code matched the new source, the encoded-space URI check passed, and the real local CLI still returned the expected next check. Build output stays local and is not a public package release.

[The first GitHub run](https://github.com/ibrahim119-IN/decilist/actions/runs/40234172464) had 271 passing Python tests and one failing URI-space reference test on Ubuntu; the sibling Windows job was cancelled by the matrix. The parser was repaired without weakening that assertion, and four host-independent encoded-space/traversal cases were added. The [original freeze](../benchmarks/triage_freeze_windows_v1.json) remains unchanged; the revised freeze declares the portability replay on previously observed cases. All evidence hashes and categorical outcomes in the replay matched the original comparison.

Retained evidence hashes (UTF-8, LF):

- `reports/triage-machine-2026-10-26.json`: `008133da471cc3799b94a59528f249e1b349c883d1c72802f123049957cda2b2`
- `reports/triage-fixture-proof-2026-10-26.json`: `de094a59020c4450c84ea20b67b2bd8d7183ad720d5f5b326e5d0332d5d9e554`
- `reports/triage-installed-example-2026-10-26.json`: `b28c2ed42d15fd1e7fbcb6776a9ebc3c10e2e614d59fe68a72b9ee466da02a3d`
- `reports/triage-portability-replay-2026-10-26.json`: `3e2a64c064f70454209c3acda4576f7f72972e73485b163d522dc4ead3cd5178`

These checks establish implementation behavior, executable fixtures, package integration, and faithful recording of the comparison. They do not establish model usefulness, human debugging-time savings, broad log-format support, complete secret detection, or production readiness. The [observed model result](triage-results.md) remains negative for added value on this small corpus.
