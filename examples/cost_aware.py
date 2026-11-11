"""Choose by the cost of each mistake, then replay past decisions. The provider is a fixed stand-in, not a model."""
import asyncio
import json
import tempfile
from pathlib import Path

from decilist import Costs, Decilist, DecisionLog, FunctionProvider, cost_report

OPTIONS = {"ignore": "Known noise", "auto_remediate": "An approved fix exists", "page_oncall": "Needs a person now"}

# costs[answered][actual]: what it costs to answer one thing when another was right.
COSTS = Costs(
    {
        "ignore": {"page_oncall": 100, "auto_remediate": 5},  # Missing a real incident is the expensive mistake.
        "auto_remediate": {"page_oncall": 40},
        "page_oncall": {"ignore": 3, "auto_remediate": 3},  # Waking someone for nothing costs less.
    },
    abstain=2,  # Sending the alert to an analyst.
)

# The stand-in's probabilities for each alert, and how an analyst closed it afterwards.
ALERTS = [
    ({"id": 1, "p": {"ignore": 0.99, "auto_remediate": 0.007, "page_oncall": 0.003}}, "ignore"),
    ({"id": 2, "p": {"ignore": 0.75, "auto_remediate": 0.05, "page_oncall": 0.20}}, "page_oncall"),
    ({"id": 3, "p": {"ignore": 0.75, "auto_remediate": 0.05, "page_oncall": 0.20}}, "ignore"),
    ({"id": 4, "p": {"ignore": 0.05, "auto_remediate": 0.93, "page_oncall": 0.02}}, "auto_remediate"),
    ({"id": 5, "p": {"ignore": 0.50, "auto_remediate": 0.05, "page_oncall": 0.45}}, "page_oncall"),
    ({"id": 6, "p": {"ignore": 0.02, "auto_remediate": 0.03, "page_oncall": 0.95}}, "page_oncall"),
]


def stand_in(question):
    # A fixed stand-in so the example runs offline. Replace it with your model.
    return json.loads(question.state)["p"]


async def main():
    with tempfile.TemporaryDirectory() as folder:
        log = DecisionLog(Path(folder) / "decisions.jsonl")
        decisions = Decilist([FunctionProvider(stand_in, name="stand-in", model="stand-in-v1")], log=log)
        triage = decisions.define("alert_triage", "Choose what to do with this alert.", options=OPTIONS, costs=COSTS)

        print("The provider is a fixed stand-in, not a real model.")
        for alert, closed_as in ALERTS:
            answer = await triage(alert)
            triage.record_outcome(answer, expected=closed_as)
            picked = answer.value if answer.status == "accepted" else "ask an analyst"
            print(f'alert {alert["id"]}: most probable {answer.decision.value} ({answer.decision.confidence:.2f}), cost rule: {picked}')

        # Replay the same labelled decisions under both rules.
        report = cost_report(log.path, COSTS, name="alert_triage", min_confidence=0.7)
        print(f'cost rule: total cost {report["cost_rule"]["total_cost"]:.0f}, wrong {report["cost_rule"]["wrong"]}, asked a person {report["cost_rule"]["abstained"]}')
        print(f'confidence 0.70: total cost {report["confidence_rule"]["total_cost"]:.0f}, wrong {report["confidence_rule"]["wrong"]}, asked a person {report["confidence_rule"]["abstained"]}')
        print("Six made-up alerts show how the rules differ. They do not measure anything.")


if __name__ == "__main__":
    asyncio.run(main())
