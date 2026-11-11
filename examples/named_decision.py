"""Define a decision once and report on it by name. The provider is a fixed stand-in, not a model."""
import asyncio
import json
import tempfile
from pathlib import Path

from decilist import Decilist, DecisionLog, FunctionProvider, threshold_table

# Each alert, and how an analyst closed it afterwards.
ALERTS = [
    ({"rule": "cpu_high", "during_backup": True, "seen_before": True}, "ignore"),
    ({"rule": "service_down", "has_runbook": True, "seen_before": False}, "auto_remediate"),
    ({"rule": "failed_logins", "critical_asset": True, "seen_before": False}, "page_oncall"),
    ({"rule": "disk_latency", "critical_asset": False, "seen_before": True}, "ignore"),
]
TOP = {"cpu_high": ("ignore", 0.9), "service_down": ("auto_remediate", 0.8), "failed_logins": ("page_oncall", 0.9), "disk_latency": ("ignore", 0.55)}


def stand_in(question):
    # A fixed stand-in so the example runs offline. Replace it with your model.
    alert = json.loads(question.state)
    if question.kind == "predict":
        seen = 0.9 if alert["seen_before"] else 0.1
        return {"true": seen, "false": 1 - seen}
    label, top = TOP[alert["rule"]]
    return {option: top if option == label else (1 - top) / 2 for option in ("ignore", "auto_remediate", "page_oncall")}


async def main():
    with tempfile.TemporaryDirectory() as folder:
        log = DecisionLog(Path(folder) / "decisions.jsonl")
        decisions = Decilist([FunctionProvider(stand_in, name="stand-in", model="stand-in-v1")], log=log)

        # Define each question once. The name goes into every log line it produces.
        triage = decisions.define(
            "alert_triage",
            "Choose what to do with this alert.",
            options={"ignore": "Known noise", "auto_remediate": "An approved fix exists", "page_oncall": "Needs a person now"},
        )
        repeat = decisions.define("alert_repeat", "Has this alert been seen before?")

        print("The provider is a fixed stand-in, not a real model.")
        for alert, closed_as in ALERTS:
            answer = await triage(alert)
            triage.record_outcome(answer, expected=closed_as)
            await repeat(alert)
            print(f'{alert["rule"]}: {answer.value if answer.status == "accepted" else "ask an analyst"}')

        # Reports can now be about one decision instead of the whole log.
        table = threshold_table(log.path, name="alert_triage", min_samples=1)
        row = next(row for row in table["rows"] if row["threshold"] == 0.7)
        print(f'alert_triage at 0.70: answered {row["covered"]} of {table["decisions"]}, {row["correct"]} of {row["labelled"]} correct')
        print("decisions in the log:", ", ".join(threshold_table(log.path)["names"]))


if __name__ == "__main__":
    asyncio.run(main())
