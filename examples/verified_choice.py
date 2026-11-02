"""Run an offline checked choice with a fixed provider."""
import asyncio

from decilist import Decilist, FunctionProvider, Policy


def fixed_provider(_question):
    # This fixed stand-in is not a real model.
    return {"primary": 0.6, "backup": 0.4}


async def main():
    decisions = Decilist([FunctionProvider(fixed_provider)])
    available = {"backup"}
    result = await decisions.choose_verified(
        {"available": sorted(available)},
        "Choose a route.",
        {"primary": "Primary route", "backup": "Backup route"},
        verify=lambda label: label in available,
        fallback=lambda: "manual routing",
        policy=Policy(min_confidence=0.0),
    )
    print("The provider is a fixed stand-in, not a real model.")
    print(f"{result.status}: {result.value}")


if __name__ == "__main__":
    asyncio.run(main())
