"""Correct an overconfident model from its own outcomes. The provider is a fixed stand-in, not a model."""
import asyncio
import json
import tempfile
from pathlib import Path

from decilist import Costs, Decilist, DecisionLog, FunctionProvider, Policy, fit_calibration

OPTIONS = {"ignore": "Known noise", "auto_remediate": "An approved fix exists", "page_oncall": "Needs a person now"}
LABELS = list(OPTIONS)
COSTS = Costs(
    {
        "ignore": {"page_oncall": 100, "auto_remediate": 5},
        "auto_remediate": {"page_oncall": 40},
        "page_oncall": {"ignore": 3, "auto_remediate": 3},
    },
    abstain=2,
)


def stand_in(question):
    # A fixed stand-in that is too sure of itself: it says 0.99 or 0.90, and is right less often than that.
    alert = json.loads(question.state)["id"]
    top = 0.99 if alert % 2 == 0 else 0.90
    return {label: top if label == LABELS[alert % 3] else (1 - top) / 2 for label in LABELS}


def closed_as(alert):
    # How an analyst closed the alert: the stand-in's 0.99 answers are right 4 times in 5, its 0.90 answers 3 times in 5.
    right = (alert // 2) % 5 != 0 if alert % 2 == 0 else (alert // 2) % 5 < 3
    return LABELS[alert % 3] if right else LABELS[(alert + 1 + (alert // 2) % 2) % 3]


async def main():
    with tempfile.TemporaryDirectory() as folder:
        log = DecisionLog(Path(folder) / "decisions.jsonl")
        decisions = Decilist([FunctionProvider(stand_in, name="stand-in", model="stand-in-v1")], Policy(min_confidence=0), log=log)

        # 1. Run for a while and record what turned out to be right.
        raw = decisions.define("alert_triage", "Choose what to do with this alert.", options=OPTIONS, costs=COSTS)
        for alert in range(100):
            raw.record_outcome(await raw({"id": alert}), expected=closed_as(alert))

        # 2. Fit a calibration from those outcomes.
        fit = fit_calibration(log.path, name="alert_triage")
        before, after = fit.report["before"], fit.report["after"]
        print("The provider is a fixed stand-in, not a real model.")
        print(f'before: says {before["mean_confidence"]:.2f} sure on average, is right {before["accuracy"]:.2f} of the time')
        print(f'after:  says {after["mean_confidence"]:.2f} sure on average, is right {after["accuracy"]:.2f} of the time')
        print(f"temperature: {fit.calibration.temperature:.2f}")

        # 3. Use it. The same alert, with and without the correction.
        calibrated = decisions.define("alert_triage.calibrated", "Choose what to do with this alert.", options=OPTIONS, costs=COSTS, calibration=fit.calibration)
        one, two = await raw({"id": 0}), await calibrated({"id": 0})
        print(f'alert 0 before: ignore at {one.decision.probabilities["ignore"]:.2f}, cost rule: {one.value or "ask an analyst"}')
        print(f'alert 0 after:  ignore at {two.decision.probabilities["ignore"]:.2f}, cost rule: {two.value or "ask an analyst"}')

        # 4. Check on 100 alerts the fit never saw.
        totals = {"before": 0.0, "after": 0.0}
        for alert in range(100, 200):
            for title, decide in (("before", raw), ("after", calibrated)):
                answer = await decide({"id": alert})
                totals[title] += COSTS.abstain if answer.status == "abstained" else COSTS.of(answer.value, closed_as(alert))
        print(f'100 new alerts, total cost before: {totals["before"]:.0f}, after: {totals["after"]:.0f}')
        print("These alerts are made up to show the mechanism. They do not measure anything.")


if __name__ == "__main__":
    asyncio.run(main())
