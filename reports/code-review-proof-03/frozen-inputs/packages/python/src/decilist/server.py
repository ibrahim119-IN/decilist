"""Installable loopback HTTP application. Importing it never loads a model."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .contracts import Decision, DecisionRequest
from .engine import Decilist
from .runtime import SCHEMA_VERSION, runtime_version
from .code_review import CodeReviewer, ReviewRequest, ReviewResult


def create_app(engine: Decilist | None = None, *, local_only: bool = True, provider: str = "demo", include_ui: bool = True, reviewer: CodeReviewer | None = None) -> FastAPI:
    if engine is None:
        from .demo import make_demo_engine
        engine = make_demo_engine()
    app = FastAPI(title="Decilist", version=runtime_version(), description="Local decision runtime", docs_url=None, redoc_url=None)
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
        return {"service": "decilist", "schema_version": SCHEMA_VERSION, "runtime_version": runtime_version(), "ready": True, "status": "ok", "provider": provider, "local_only": local_only, "providers": [{"name": p.name, "model": p.model, "revision": getattr(p, "revision", None), "locality": p.locality} for p in engine.providers]}

    @app.post("/v1/decisions", response_model=Decision)
    async def decide(body: DecisionRequest):
        policy = body.policy.model_copy(update={"local_only": local_only or body.policy.local_only})
        return await engine.decide(body.question, policy)

    @app.post("/v1/code-review", response_model=ReviewResult)
    async def review(body: ReviewRequest):
        if reviewer is None:
            return JSONResponse({"error": "code_review_not_loaded", "hint": "Start decilist review serve on port 8766"}, status_code=503)
        return await reviewer.review(body.code, task=body.task, language=body.language, policy=body.policy)

    if include_ui:
        from .web import attach_ui
        attach_ui(app, engine)
    return app
