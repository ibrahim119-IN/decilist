"""Loopback development API. Remote deployment/authentication is outside this MVP."""
from __future__ import annotations

import os
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from decilist import Decision, DecisionRequest, Decilist, LiquidLocalProvider, Policy
from examples.workflow import make_engine


def create_app(engine: Decilist | None = None, *, local_only: bool = True) -> FastAPI:
    engine = engine or make_engine()
    app = FastAPI(title="Decilist", version="0.1.0-alpha.1", description="Local development decision runtime", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"error": "cross_origin_not_allowed"}, status_code=403)
        if request.method == "POST":
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"error": "application_json_required"}, status_code=415)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 262144:
                    return JSONResponse({"error": "request_too_large"}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.get("/health")
    async def health():
        return {"status": "ok", "local_only": local_only, "providers": [{"name": p.name, "model": p.model, "locality": p.locality} for p in engine.providers]}

    @app.post("/v1/decisions", response_model=Decision)
    async def decide(body: DecisionRequest):
        policy = body.policy.model_copy(update={"local_only": local_only or body.policy.local_only})
        return await engine.decide(body.question, policy)

    root = Path(__file__).resolve().parents[1] / "playground"

    @app.get("/", include_in_schema=False)
    async def playground():
        return FileResponse(root / "index.html")

    @app.get("/app.js", include_in_schema=False)
    async def script():
        return FileResponse(root / "app.js", media_type="text/javascript")

    @app.get("/style.css", include_in_schema=False)
    async def styles():
        return FileResponse(root / "style.css", media_type="text/css")

    return app


def from_environment():
    provider = os.environ.get("DECILIST_PROVIDER", "demo")
    if provider == "demo":
        return create_app()
    if provider == "liquid":
        model = LiquidLocalProvider.load(allow_model_code=os.environ.get("DECILIST_ALLOW_MODEL_CODE") == "1")
        return create_app(Decilist([model], Policy(timeout_ms=30000)))
    raise ValueError("DECILIST_PROVIDER must be demo or liquid")
