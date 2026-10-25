# First independent example: request desk

The example is not tied to an industry. An existing text application already knows its current document and destination. A user asks for a summary, outline, or checklist; the app uses Decilist to choose whether to prepare a local request preview, ask one focused question, or request review. This is decision routing, not document generation. Developers supply their own state, question, choices, and provider inside their own application.

## Five-minute application walkthrough

1. Start the explicitly selected runtime and open `/demo`. State whether this run uses rules or the verified local model; the interface also shows the mode.
2. Enter `Prepare a short summary of the project update.` Show the local preview and the existing document context. Nothing is sent or published.
3. Enter `Prepare something from this document.` Answer the single clarification with `A short outline` or `عايز ملخص قصير`. The original request and known document stay in place.
4. Enter an unsupported or unclear request, such as `Maybe a summary or a checklist, I'm not sure.` Show review. Actual model output may differ from the authored baseline; preserve and explain the observed result.
5. Reveal the short direct `choose` call in `decilist/web.py`: input plus existing app context, instructions, and allowed outcomes. The app owns the wording and action mapping. No decision registration, account, or Playground is required.
6. Open the optional Playground. Try input → inspect answer → correct/save local test → rerun → copy integration in the same workspace. Advanced details remain collapsed until useful.
7. Show the measured report with coverage and errors. Distinguish this app example from the separate request-text evaluation task. Do not use baseline accuracy as evidence of model intelligence.

The complete UX and verification scope is recorded in [UI experience](ui-experience.md). Before the November 1 presentation, rehearse the actual selected provider offline after dependencies and weights are cached, and keep the clearly labelled baseline as a fallback demonstration.

## Additional foundation checks: workflow gate

1. Open the local Playground. Point to the provider names: `uniform-v1` and `rules-v1`. Say explicitly that these are baselines.
2. Run the default transient-failure state. The uniform result fails the 70% confidence threshold; the rules select `retry`. Inspect both attempts.
3. Click Ready and run again: `proceed`. Click Needs review: `review`.
4. Switch to Predict to see a boolean and P(true), then Score to see ordered readiness. These example rules support only their documented questions.
5. Set confidence to 0 and rerun Choose. The uniform provider can now be accepted; its declared-order tie-break illustrates why relaxing a threshold may lower useful quality.
6. Restore 70%, replace the state with malformed JSON, and run. The rules fail and the system abstains. No action is silently invented.
7. Run the TypeScript example against the same Python server. Show the saved evaluation report and explain its limitations.

## Planned model walkthrough

After the real local-inference acceptance gate, lead with the normal application's request flow: a complete request proceeds, missing information triggers one focused question in place, and uncertainty is handled explicitly. Users should not see model settings, raw JSON, or Decilist configuration.

Then reveal the short direct SDK integration. No decision registration or Playground visit is required. Show the optional Playground only to explain how the developer tries an example, corrects an answer, runs tests, and copies integration code. Display measured local-model results with their limitations. Do not call the foundation demo an AI-quality demonstration. Keep that clearly labeled baseline demo available if the model environment fails during the presentation.

## One-sentence pitch

Decilist lets developers add tested decisions directly to their existing applications, with interchangeable providers, local operation, and explicit handling of uncertainty.
