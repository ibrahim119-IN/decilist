# V8 development: better source coverage, no demonstrated repair gain

This candidate extends the terminal repair workflow with bounded selection of
observed value producers and separately qualified module literal edits. The
three historical cases below were already exposed during V7. They are development
replays, not fresh accuracy observations or a developer-time comparison.

The source and prompt were fixed throughout this run. The local Qwen3.6-27B Q8
profile and native non-thinking template were verified before generation. No
test, reference repair or production fixture was changed to help the assistant.

| Exposed historical case | Automatic protected repair | Actual result |
| --- | --- | --- |
| NAT-01, digest authentication | No | The computation was now supplied. An encoding change failed the test; the second response repeated that intervention and was rejected. |
| NAT-03, proxy exclusion | No | The diagnosis identified the scheme-qualified entry, but the generated replacement contained invalid Python source and was rejected before patching. |
| NAT-04, URL authority parsing | No | The relevant module regex leaf was supplied and editable. The proposed pattern failed tests; its repeated revision was rejected. |

All three original participant projects remained without an applied repair.
The new source coverage and validation capabilities do not establish better
diagnosis, repair quality or lower elapsed developer time. One earlier V7
historical pair was faster; that result does not transfer to this candidate.
The [complete exposed development record](../reports/triage-v8-exposed-development-01.json)
retains the failed generations, actual verification and source identities. Its
elapsed values are operational records only: non-timed unit tests could run
concurrently. No fresh benchmark or human productivity result is claimed.

Root verification ran the complete suite: **2,010 tests and 32 subtests passed**.
The wheel and source distribution built successfully. An isolated installation
outside the checkout ran the actual `decilist debug` command on the already
exposed simple d01 development case: it applied the exception-preservation repair
and passed its protected checks. This verifies packaging and the terminal route;
it does not offset the three historical failures or demonstrate time savings.
[Installed-command record](../reports/triage-v8-installed-01.json).

## Implemented boundaries

The selector now follows a bounded association from a failing subscript read to
recorded same-class setter calls and an observed, syntactically unambiguous RHS
producer at the same caller and line. Multiline failing assertions use their
actual enclosing assertion span. This identifies candidates; it does not observe
matching container keys, receiver identity, invocation identity or causality.
Two producers and four new bodies share fixed source/count/depth budgets.

Module literal evidence stays separate from runtime-observed functions. Only
clean canonical assignments referenced on executed lines of retained
implementation source can qualify. A referenced compiled pattern is ranked
before small string constants; this is a selection heuristic. A read-only or
redacted function never gains edit permission merely because its separate clean
module assignment qualifies. Parent evidence, leaf IDs and model-visible
permissions remain synchronized under the existing 24,000-character limit.

The `.4` edit contract permits a single str/bytes token, a pattern literal in an
existing canonical `re.compile` assignment, or one string keyword leaf of an
existing static literal-template format call. The unchanged template may contain
bounded adjacent string literals. Callable identity, imports, assignment name,
flags, template and other leaves stay fixed. Function and literal edits share the
two-edit limit. Multiple edits of the same assignment still reject overlap.

The runner reconstructs literal edits from the original snapshot bytes, checks
file/source hashes and exact citations, requalifies bindings and token spans,
and applies the unchanged whole-file AST screen before writing. Actual failing
and regression tests still determine whether application is allowed. This is a
trusted-project workflow under the caller's OS permissions, not an OS sandbox.

## Remaining work

Improve the hypothesis and edit-generation step, retain unsuccessful attempts,
and repeat exposed development before freezing a candidate for the four untouched
round-two cases. Those cases have passed root-owned fixture/worker verification,
but no assistant inference has used them. Their prospective eight-slot measurement
implementation is still a private draft and has not produced timings.
