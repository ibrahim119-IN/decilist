# First triage result: implementation works, added model value is unproven

Measured October 26, 2026 Cairo time (October 25 UTC). Source: commit `e0cc931`, with exact evaluation file hashes in [the original pre-run freeze](../benchmarks/triage_freeze_windows_v1.json). Other documentation/study work and a pre-existing example were uncommitted at measurement, so the report correctly records `source_dirty=true`.

The optional assistant runs on saved test logs, gathers bounded referenced source excerpts, and chooses a next check through Decilist. A separate clean environment installed the built Python wheel and successfully used its CLI outside the source checkout against the existing local Liquid runtime. No repair was generated or applied by the assistant.

## What was actually tested

Ten fixtures use the real Decilist Python source at pinned revision `bf2a1df`: eight regressions, a passing control, and a failing run whose useful evidence is deliberately withheld. Three regression fixtures restore historical source; one of those simulates a Torch capability rather than invoking a native GPU kernel. The remaining regressions are explicitly seeded defects. This is a reproducible feasibility corpus, not a collection of ten naturally occurring production incidents.

For every fixture, execution verified the unchanged baseline, the expected mutation outcome, and a passing reference repair. Baseline and repaired states each passed three protected parametrized checks. The missing-import mutation fails collection as expected. Tests, manifests, noneditable source, and source stability during verification are checked before accepting a repair. Passing these targeted checks does not prove all possible behavior correct.

Four regressions are development cases; four regressions and two controls are evaluation cases. The cases, acceptable next-check labels, instructions, policy (0.7 confidence, local-only, 30 seconds), and error-pattern comparator were frozen before model calls. Authors also authored the labels and comparator; there is no independent human label review. Oracle labels and reference repairs never enter the model input.

## Observed comparison

| Unique cases | Automated error-pattern comparator | Local Liquid d1-3B |
| --- | --- | --- |
| 4 development regressions | 4 acceptable suggestions | 1 acceptable suggestion, 3 abstentions |
| 4 evaluation regressions | 4 acceptable suggestions | 0 suggestions, 4 abstentions |
| Passing and insufficient-evidence controls | No specific suggestion | No specific suggestion; no inference decision |
| Overall regression coverage | 8/8 | 1/8 |
| Wrong accepted next checks | 0/8 accepted | 0/1 accepted |

Liquid's seven abstentions were recorded as `low_confidence`, not provider crashes. Zero wrong accepted suggestions out of one is not meaningful evidence of reliable diagnosis. The unchanged threshold was not reduced after observing these results. The existing runtime reports model revision `051bcc464b01b9f92942b364d9586b0ef5912432` and local provider `liquid-local`.

Each case was repeated three times for timing/stability; all outcomes stayed the same. Quality counts use only the first observation of each unique case. Repetitions are not extra independent test cases. Both methods received identical evidence hashes.

Median routing time over 24 regression attempts was **0.156 ms** for error patterns and **181.486 ms** for Liquid, using the already loaded local model. These times exclude evidence preparation, test execution, model loading, reading, investigation, and repair. They measure neither developer productivity nor end-to-end debugging time. The comparator is an automated regex baseline, not a human's normal workflow.

## Example actually run from the installed package

The deliberately seeded missing-import case produced `ModuleNotFoundError`. The installed CLI selected `dependency_environment` and pointed to actual lines in the failing test, package initializer, and `providers.py`. Its instructions say to check whether an import is declared and valid; they do not authorize installing the invented fixture dependency. This individual demonstration proves packaging, transport, evidence linking, and one successful bounded suggestion. It does not establish benchmark-wide usefulness.

## Human experiment and decision

The two-case pilot is prepared locally with both timers unset. The participant will use their ordinary tools, then ordinary tools plus Decilist on a distinct matched case. Only a repair passing the protected checks ends a solved trial. Interruptions, failures, hint latency, tools reported by the participant, and unsolved cases remain in the record. Instructions: [deferred study](triage-study.md).

**Human debugging-time savings are not measured.** The current model abstained on both pilot cases in the machine evaluation, so this prepared version can exercise the workflow but should not be presented as a promising efficacy trial. The cases are not established to be equally difficult. One participant and two cases cannot support a general speedup claim. If improving the assistant, freeze its new version and use fresh, prespecified cases before a broader time comparison.

The immediate product decision is to improve specific, evidence-backed help before adding integrations or UI screens. Test a clearly labelled rules path for direct error signals and reserve model evaluation for ambiguous evidence; that hybrid production path is a proposal, not implemented functionality. Keep the existing negative result. The [Arabic roadmap](product-roadmap-ar.md) gives the November 1 priorities and success gates.

## Retained evidence

- [Machine comparison, raw decisions, evidence, hashes and limitations](../reports/triage-machine-2026-10-26.json)
- [Observed baseline, failure and reference-repair proof](../reports/triage-fixture-proof-2026-10-26.json)
- [Installed-package CLI example](../reports/triage-installed-example-2026-10-26.json)
- [Frozen corpus](../benchmarks/triage_cases.json) and [original source/prompt freeze](../benchmarks/triage_freeze_windows_v1.json)

Raw unsanitized subprocess logs stay in local isolated lab directories. Published logs are bounded and sanitized; redaction is heuristic. See [verification](triage-verification.md) for software checks actually run.

## Portability follow-up

The first GitHub run exposed a real Ubuntu parsing defect: a percent-encoded space in a project-local `file://` reference was not decoded in one sanitized URI branch. The existing assertion was retained; new host-independent space/traversal checks reproduce the branch on Windows too. The fix decodes that URI path before the existing confinement checks.

The original freeze and report above are preserved. A [new source freeze](../benchmarks/triage_freeze.json) explicitly identifies this portability-only change and the earlier observed result. A [follow-up replay](../reports/triage-portability-replay-2026-10-26.json) used the same retained proof, labels, instructions, policy, rules, and model. All 60 provider/case/repetition input hashes and categorical outcomes matched the original run. These are already-seen cases, not new held-out evidence; the result remains one model suggestion and seven abstentions across the eight unique regressions.
