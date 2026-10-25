# Deferred developer triage pilot

This experiment is prepared for a later walkthrough. Preparing it does not start a participant clock, request a model hint, apply a repair, or produce a human performance result. Human time saved remains unmeasured until the participant completes the protocol.

The comparison is **normal logs + source + the same existing tools**, versus **the same workflow with an optional Decilist next-check hint**. Both cases keep their original failing pytest log. Decilist supplies a bounded inspection suggestion, never a code repair or an executable command.

Current readiness is limited to preparing and recording the experiment. The [frozen local-model run](triage-results.md) selected one acceptable route under the frozen labels across eight Python/pytest regressions and abstained on the other seven; the automated rules comparator selected acceptable routes for all eight. Both paired study cases currently abstain, so the hint is likely to offer manual review. No human benefit has been demonstrated. A later walkthrough can inspect the developer experience, but a meaningful efficacy comparison needs a prespecified improved assistant and fresh cases. Do not tune this session's threshold or silently substitute a different model to produce a favorable result.

## Participant walkthrough

Wait until the operator has prepared the default session and checked the fixtures. Do not inspect the prepared case folders, tests, original logs, other case, lab implementation, case catalog, or reference repairs before starting. That would make the later elapsed time hard to interpret.

When you choose to try it, use the existing repository test environment from the checkout root and run:

```bash
python -m benchmarks.triage_study start
```

This is the only initial action. It starts the next case's actual clock and prints its isolated workspace, original failing log, allowed production file, condition, and trusted verification command. Open those files after the clock has started. Leave the other prepared case unopened until its own `start`.

Work as you normally would using the same available tools in both cases. Record major tools and commands as participant notes; the study does not monitor the editor or shell history:

```bash
python -m benchmarks.triage_study note --tool "Existing editor and source search"
python -m benchmarks.triage_study note --command "The command I actually ran"
```

These notes are annotations only. The study never executes the supplied text. Record actual use rather than copying these placeholder examples into the result. Ordinary tool use is self-reported; the study directly observes only its fixed lab test commands and hint requests. If your normal tools include another assistant, keep its availability the same in both cases and record its use.

In the **Decilist** case, you may request the extra hint:

```bash
python -m benchmarks.triage_study hint
```

It shows the same status, next check, observed failure lines, source references, confidence caveat, and limitations as the normal triage CLI. The full response is saved locally. The normal-tools case refuses this command. Hint preparation and request waiting time count toward the case's elapsed time; hint latency is also recorded separately. A failed/unavailable hint remains part of the record, rather than becoming a successful answer.

Edit only the production file listed by `start`. Keep every test and all other frozen files unchanged. When you believe the repair is ready:

```bash
python -m benchmarks.triage_study verify
```

The tool runs the lab's fixed trusted pytest command in the isolated case. A solved endpoint requires the original multi-input regression tests to pass, all protected hashes to match, the source to remain stable during execution, and completion within the cap. A failed check keeps the case active so you can continue. A protected-file change makes the trial invalid. Clicking or reporting “finished” cannot create a solved result.

After the first case ends, run `start` again for the other prepared case. Each case has its own clock. Use `status` afterward to inspect the recorded outcomes; it does not compute a general speedup estimate.

## Interruptions and unfinished work

The default cap is 1,800 active seconds per case. The configured value is printed at start. Record unrelated interruptions as they happen:

```bash
python -m benchmarks.triage_study interrupt --reason "Unrelated interruption"
python -m benchmarks.triage_study resume
```

Only explicitly recorded interruption intervals are excluded from active elapsed time. Wall elapsed time is retained too. Hint waiting, test execution, ordinary searching, and thinking remain included. Do not pause for normal task difficulty or model latency. No application-focus monitoring or retrospective guess about interruptions is performed.

To stop without a verified repair, use `stop --reason unsolved`, or `stop --reason withdrawn` if you withdraw. `stop --reason cap` is accepted only after the configured cap. The cap is checked when a study command runs; there is no background alarm or process that stops the editor exactly at the deadline. A passing verification after the cap records a verified repair but a **capped** trial, not a within-cap solved endpoint. Clock regressions produce invalid timing with no invented duration.

## Preparation and counterbalancing — operator

Do not ask the participant to start now. After the corpus proof is verified, prepare the default session with:

```bash
python -m benchmarks.triage_study create
```

The tool creates two new case folders and their original failing logs below `<workspace>/work/triage-lab/developer-study`. It leaves `started_at`, `ended_at`, and human durations null. Existing directories are never overwritten. Preparation pytest timestamps and process durations are labeled as preparation, not participant work. The lab uses the existing test interpreter; it installs no dependencies. `DECILIST_TRIAGE_PYTHON` can name the already configured foundation interpreter when needed.

The frozen neutral pair is `t05`/`t06`. They are distinct variants matched by production component and protected test breadth; there is no evidence that their human difficulty is equal. Expected routes, mutation descriptions, and reference repairs remain in the operator's lab source/catalog, outside participant manifests and model evidence. Participants must not inspect that material until both cases end. Protected assertion source is legitimate debugging evidence available in both arms; a hidden reference patch is not.

The four sequences balance condition order and case-to-condition assignment across sessions:

| Sequence | First case | Second case |
| --- | --- | --- |
| 0 | Normal tools, t05 | Same tools + Decilist, t06 |
| 1 | Same tools + Decilist, t05 | Normal tools, t06 |
| 2 | Normal tools, t06 | Same tools + Decilist, t05 |
| 3 | Same tools + Decilist, t06 | Normal tools, t05 |

An operator can use `create --session <new-path-below-lab-root> --sequence 1 --participant P02 --cap-seconds 1800` for another preassigned session. Give the participant the corresponding `start --session <path>` command. Do not choose the sequence after seeing a person's outcomes, and do not treat repeating these now-familiar cases as independent observations. The local alias need not contain personal details.

## Evidence and acceptance

The study uses the same `prepare_case_evidence` helper as the machine benchmark: observed saved log, real isolated source/test references, actual test command, and observed exit code. It supplies no oracle route, provenance label, reference patch, or expected repair to the model. If production source changed after the saved run, the shared helper omits potentially stale source excerpts and records why. Original baseline logs remain intact in both conditions.

The lab verifies the frozen manifest against a registry outside participant case folders and recomputes protected source/test hashes from the pinned base and corpus. Tests cannot be replaced with weaker assertions, deleted, or reverted wholesale to manufacture success. Only the declared production file can change. Verification also checks that its bytes did not change during the test run. These are isolated working copies and subprocess/import checks, not a general security boundary for hostile code.

`session.json` records actual UTC event timestamps, active/interruption intervals, observed trusted commands and output hashes, participant-reported notes, actual hint duration/report, verification results, cap/unsolved/withdrawn/invalid outcomes, and final elapsed times. Files are local; ordinary-tool annotations and local session records are not a tamper-proof audit trail. Baseline logs and case manifest hashes are checked before continuing. Study commands serialize their own state writes; if a command crashes and leaves `.study.lock`, the operator should confirm no study command is running before recovering that lock, preserving the original records.

Newly prepared sessions also record study-source, lab-catalog, and freeze-file hashes. Every hint records the shared diagnosis-contract hash (instructions, routes, and frozen lab policy), implementation-file and loaded-code hashes before/after the call, and returned runtime/model revision metadata when available. A mid-call source/contract change is flagged as unstable; do not pool it as an unchanged treatment. Existing ready sessions remain readable, so later assistance versions are declared in their hint entries rather than rejected or presented as the original version. Missing provenance in an older record remains missing, not reconstructed as observed history.

The experiment reports time to a repair validated by these frozen checks. It does not prove that a change is correct for every possible input. Two cases, one project, unknown difficulty differences, participant familiarity, and carryover between related cases prevent a claim of general or statistically established developer speedup. Counterbalancing helps expose order effects across sessions; it does not remove those limitations. Machine next-check accuracy, request latency, hint use, and human repair time must remain separate results.

The executable cases are Python/pytest only. Unit-tested JavaScript/TypeScript source-reference parsing is not an end-to-end result for Jest, Vitest, or other runners.
