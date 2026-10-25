"""Repository entry point for the same installable local runtime and optional UI."""
from __future__ import annotations

import os

from decilist import Decilist, LiquidLocalProvider, Policy
from decilist.server import create_app

__all__ = ["create_app", "from_environment"]


def from_environment():
    provider = os.environ.get("DECILIST_PROVIDER", "demo")
    if provider == "demo":
        return create_app(provider="demo")
    if provider == "liquid":
        model = LiquidLocalProvider.load(allow_model_code=os.environ.get("DECILIST_ALLOW_MODEL_CODE") == "1")
        return create_app(Decilist([model], Policy(timeout_ms=30000)), provider="liquid")
    raise ValueError("DECILIST_PROVIDER must be demo or liquid")
