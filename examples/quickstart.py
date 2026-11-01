"""The README's first program. Your own rules, no model and no server."""
import asyncio
import json

from decilist import Decilist, FunctionProvider


def my_rules(question):
    order = json.loads(question.state)
    winner = "refund" if order["days_since_purchase"] <= 30 else "escalate"
    return {"refund": float(winner == "refund"), "escalate": float(winner == "escalate")}


decisions = Decilist([FunctionProvider(my_rules, name="my-rules")])


async def main():
    answer = await decisions.choose(
        '{"days_since_purchase": 12}',
        "Pick the next step for this support request.",
        {"refund": "Refund the customer", "escalate": "Send to a person"},
    )
    if answer.status == "accepted":
        print("Do this:", answer.value)
    else:
        print("No answer. Ask a person.")


if __name__ == "__main__":
    asyncio.run(main())
