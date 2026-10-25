"""Run against the local demo API, after starting the server."""
import asyncio
import json
from decilist import Client
from examples.workflow import INSTRUCTIONS, OPTIONS


async def main():
    state = json.dumps({"complete": False, "valid": True, "transient": True, "retries": 1})
    async with Client() as sdk:
        choice = await sdk.choose(state, INSTRUCTIONS, OPTIONS)
        predicate = await sdk.predict(state, "Is the workflow ready to proceed?")
        score = await sdk.score(state, "How ready is the workflow?", ["Needs review", "Can retry", "Ready"])
    assert choice.status == predicate.status == score.status == "accepted"
    assert choice.value == "retry" and predicate.value is False and score.value == 1.0
    print("Python HTTP SDK: choose=retry, predict=false, score=1.0")


if __name__ == "__main__":
    asyncio.run(main())
