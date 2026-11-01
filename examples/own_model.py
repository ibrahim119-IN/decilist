"""Connect your own model. StandInModel is not trained: replace it with yours."""
import asyncio
import json

from decilist import Decilist, FunctionProvider

OPTIONS = {"urgent": "Handle this today", "normal": "Handle this in the usual queue"}
INSTRUCTIONS = "How should this support request be handled?"


class StandInModel:
    def predict_proba(self, features):
        urgent = {0: 0.1, 1: 0.6}.get(features["failed_payments"], 0.9)
        return {"urgent": urgent, "normal": 1 - urgent}


model = StandInModel()


def my_model_provider(question):
    return model.predict_proba(json.loads(question.state))


decisions = Decilist([FunctionProvider(my_model_provider, name="my-model", model="stand-in-v1")])


async def handle(failed_payments):
    state = json.dumps({"failed_payments": failed_payments})
    answer = await decisions.choose(state, INSTRUCTIONS, OPTIONS)
    if answer.status == "accepted":
        return f"{answer.value} (confidence {answer.confidence:.2f}, from {answer.model})"
    # 0.6 is under the default min_confidence of 0.7, so the app decides what to do.
    return f"abstained: ask a person (attempt: {answer.attempts[0].outcome})"


async def main():
    for failed_payments in (0, 1, 3):
        print(f"{failed_payments} failed payments ->", await handle(failed_payments))


if __name__ == "__main__":
    asyncio.run(main())
