"""Optional local developer workspace and an app-owned request-composer example."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, StringConstraints

from .contracts import Policy
from .demo import APP_INSTRUCTIONS, APP_OPTIONS, app_review_reason
from .engine import Decilist


class AppRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
    ]
    clarification: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=1000)
    ] = ""


def attach_ui(app: FastAPI, engine: Decilist) -> None:
    root = Path(__file__).with_name("static")

    @app.get("/", include_in_schema=False)
    async def playground():
        return FileResponse(root / "index.html")

    @app.get("/demo", include_in_schema=False)
    async def demo():
        return FileResponse(root / "demo.html")

    @app.get("/app.js", include_in_schema=False)
    async def script():
        return FileResponse(root / "app.js", media_type="text/javascript")

    @app.get("/demo.js", include_in_schema=False)
    async def demo_script():
        return FileResponse(root / "demo.js", media_type="text/javascript")

    @app.get("/style.css", include_in_schema=False)
    async def styles():
        return FileResponse(root / "style.css", media_type="text/css")

    @app.post("/v1/demo/request")
    async def prepare_request(body: AppRequest):
        # Known context belongs to the application; the user does not re-enter it.
        state = {
            "app": "request-desk-v1",
            "current_document": "Project update",
            "destination": "Private drafts",
            "request": body.request,
            "clarification": body.clarification,
        }
        result = await engine.choose(
            json.dumps(state, ensure_ascii=False),
            APP_INSTRUCTIONS,
            APP_OPTIONS,
            policy=Policy(local_only=True, min_confidence=0.7, timeout_ms=30000),
        )
        # Guardrails belong to the app. Keep the actual SDK result, probabilities,
        # and confidence below even when the app declines its selected next step.
        guard_reason = app_review_reason(body.request, body.clarification)
        outcome = "review"
        message = "This request needs a closer look. Your request is kept here so you can revise it."
        if result.status == "abstained":
            failed = any(
                a.outcome in {"error", "timeout", "invalid_output", "blocked"}
                for a in result.attempts
            )
            if failed:
                outcome = "unavailable"
                message = (
                    "We couldn't check this request right now. "
                    "Your text is kept here. Please try again."
                )
            else:
                message = (
                    "There isn't enough certainty to prepare this draft. "
                    "Please make your request more specific."
                )
        elif guard_reason:
            outcome = "review"
        elif result.value == "clarify" and not body.clarification:
            outcome = "clarify"
            message = (
                "What would you like from the project update: "
                "a summary, outline, or checklist?"
            )
        elif result.value == "proceed":
            # The app owns execution and only prepares a local preview. The model
            # cannot authorize consequential actions, even if it misclassifies one.
            outcome, message = "proceed", "Your request draft is ready."
        draft = None
        if outcome == "proceed":
            draft = {
                "request": body.request,
                "clarification": body.clarification,
                "source": "Project update",
                "destination": "Private drafts",
            }
        return {
            "outcome": outcome,
            "message": message,
            "draft": draft,
            "decision": result.model_dump(),
        }
