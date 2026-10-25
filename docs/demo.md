# First independent example: workflow gate

The example is not tied to education, construction, animation, GIS, or finance. It asks whether an application has enough validated evidence to proceed, should retry a transient failure, or should ask for review. Developers supply their own state, question, options, and provider for their domain.

## Five-minute foundation walkthrough

1. Open the local Playground. Point to the provider names: `uniform-v1` and `rules-v1`. Say explicitly that these are baselines.
2. Run the default transient-failure state. The uniform result fails the 70% confidence threshold; the rules select `retry`. Inspect both attempts.
3. Click Ready and run again: `proceed`. Click Needs review: `review`.
4. Switch to Predict to see a boolean and P(true), then Score to see ordered readiness. These example rules support only their documented questions.
5. Set confidence to 0 and rerun Choose. The uniform provider can now be accepted; its declared-order tie-break illustrates why relaxing a threshold may lower useful quality.
6. Restore 70%, replace the state with malformed JSON, and run. The rules fail and the system abstains. No action is silently invented.
7. Run the TypeScript example against the same Python server. Show the saved evaluation report and explain its limitations.

## Planned model walkthrough

After the real local-inference acceptance gate, repeat with `liquid-local`, display measured results, and compare the same dataset. Do not call the foundation demo an AI-quality demonstration. Keep the baseline demo available if the model environment fails during the presentation.

## One-sentence pitch

Decilist lets developers define a decision once, run it locally or through an interchangeable provider, and measure when to trust it before their application acts.
