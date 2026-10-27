# Historical transfer: one faster repair, overall gate failed

The improved candidate was frozen before opening the three historical cases. All six assigned fresh-agent sessions completed in the declared order. One of three initial local-assistant calls supplied a verified repair; the other two required manual continuation. The overall quality and time gates failed. No human productivity was measured.

| Historical defect | Ordinary tools | With assistant | Assistant minus ordinary | Initial assistance |
| --- | ---: | ---: | ---: | --- |
| Digest authentication, NAT-01 | 32.734s | 82.141s | +49.407s | Failed candidate, then no safe intervention |
| Scheme-specific proxy exclusion, NAT-03 | 48.218s | 23.797s | −24.421s | Verified automatic repair |
| Userinfo parsing, NAT-04 | 44.782s | 61.641s | +16.859s | No safe intervention |

The descriptive median was **44.782s ordinary versus 61.641s assisted**, approximately **37.6% slower** with the assistant. Only 1/3 pairs was faster, versus the required 2/3. Initial assistant repair rate was 1/3, versus the required 2/3. All six final participant projects passed their complete protected collections in independent root reruns: 8, 14 and 83 checks per defect. Failed assistance, waiting and manual continuation remain in the times.

NAT-03 demonstrates a real bounded saving in this experiment: its tested automatic repair finished 24.421 seconds earlier than the paired ordinary-tools session. Selecting that success alone would conceal the overall negative result. These are three same-project historical defects and six GPT-6.1-sol/xhigh sessions, not a representative sample of developers or independent human tasks. Times are warm; download, installation and model loading are separate.

## Source and test provenance

All cases use exact historical HTTPX production code, with its BSD-3-Clause license, rather than fabricated source mutations:

- NAT-01: [HTTPX #3045](https://github.com/encode/httpx/pull/3045), broken `ca51b4532a4495c38c8abf1a393dbd80099deae3`, fixed `99cba6ac642b12f0331d069ae324402c54c4c600`. The complete upstream authentication test module was retained, without upstream project conftest/configuration.
- NAT-03: [HTTPX #2741](https://github.com/encode/httpx/pull/2741), broken `2318fd822cdb16435ccb5cabcba16c0b7969c1e4`, fixed `3b9060ee1121b48669e0b30045c9344065f4f2ae`. The original table/assertion was retained; synthetic `getproxies` input replaced system environment/registry access. Two additional unchanged invariants cover wildcard precedence and proxy-value normalization.
- NAT-04: [HTTPX #2986](https://github.com/encode/httpx/pull/2986), broken `5b5f6d8e172910371b5901f6e21717097274c513`, fixed `b471f01d668b9ef730e8843531b07b1c21b74c47`. The complete upstream URL test module was retained, without upstream project conftest/configuration.

Original broken/fixed checks, pinned identities, licenses and isolated-worker compatibility were verified before candidate exposure. Public historical fixes may have appeared in model training. NAT-02 was excluded before outcomes because of a conservative prior-family overlap; it was not substituted after a failure.

## Semantic audit and remaining limitations

Root inspected the final edits in addition to rerunning tests. The authentication repairs remove duplicate digest input. Both proxy repairs preserve scheme-qualified entries while retaining wildcard behavior.

The assisted NAT-04 manual continuation changed an additional component-validation regex and rewrote line endings, beyond the minimal authority-parser edit in the ordinary arm. A separate isolated check found a behavior difference: `URL(..., userinfo=b"user:p@ss")` is rejected by the ordinary repair but accepted and escaped by the assisted repair; encoded input remains accepted by both. This extra behavior was outside the frozen 83 checks. The original protected-pass result is retained, but it must not be advertised as proof of a minimal contract-preserving or exhaustive semantic repair. Participant trees were not edited after their clocks ended.

The actual NAT-01 model context omitted the digest computation while retaining header getters/normalizers, so the model proposed an irrelevant encoding change. NAT-04 required module-level regex changes outside the candidate's editable function-body scope. These are concrete capability gaps for the next candidate: bounded retrieval of observed value-producing code and separately validated module-literal edits. They do not justify permitting arbitrary new calls or weakening protected tests.

The exact 72-file candidate snapshot, native mode/weights/runtime identity, complete traces, source diffs and raw timing records remain on L. [Full six-slot report](../reports/triage-natural-v7-01.json), [out-of-band semantic audit](../reports/triage-natural-v7-semantic-audit-01.json), and [predeclared protocol](triage-natural-transfer-protocol.md) retain the actual results and limits. Four further untouched defects from two other packages are being prepared independently; no assistant call has used them.
