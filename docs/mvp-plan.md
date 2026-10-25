# MVP plan — presentation Sunday, November 1, 2026

All dates use Africa/Cairo. The first commit is a foundation milestone; this schedule is a plan, not evidence that future work is complete.

| Date | Deliverable | Acceptance gate |
| --- | --- | --- |
| Sun Oct 25 | Public repository, contracts, Python runtime, TypeScript HTTP SDK, local Playground, tests | Both suites pass; browser and TS-to-HTTP demo verified; initial commit pushed |
| Mon Oct 26 | Pinned d1-3B environment and local inference | All three primitives return valid distributions on target hardware; failures recorded; no silent model substitution |
| Tue Oct 27 | Evaluation dataset and real local report | Held-out cases, accuracy/coverage, cold/warm latency, memory and reproducible commands |
| Wed Oct 28 | SDK usability and error handling | New developer can run it from a clean checkout; stable signatures; integration snippets for another HTTP language |
| Thu Oct 29 | Optional OpenAI Decisions comparison | Only if API access is available; exact contract tests, remote opt-in, measured cost; local path remains sufficient |
| Fri Oct 30 | Presentation rehearsal | 5-minute live workflow, uncertainty/fallback demonstration, clear distinction between measured and planned |
| Sat Oct 31 | Freeze and backup | Tagged demo candidate after tests, recorded offline fallback, setup instructions, clean run on presentation machine |
| Sun Nov 1 | Presentation | Rehearsed demo and honest results; no unverified model-speed or cost claims |

## MVP success criteria

1. Developers define bounded decisions without writing model-specific parsing.
2. Python and TypeScript agree on values and errors through shared fixtures.
3. A local model and baseline run the same example with a provider change.
4. Invalid or uncertain results trigger an inspectable fallback or abstention.
5. Local-only mode never sends a question to a declared remote provider.
6. A saved evaluation report supports every comparison claim.

## Deferred

Mobile apps, multimodal API, industrial templates, fine-tuning, managed hosting, production authentication, multi-tenant storage, native TS model loading, high-throughput batching, hard GPU cancellation, automatic calibration, full benchmark leaderboards, registry publication, and a completed OpenAI adapter.

The project sells a shorter path from decision definition to a measured, locally deployable implementation. It does not sell a new model or a universal decision oracle.
