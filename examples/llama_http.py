"""Serve the existing decision HTTP contract using an already running local model.

    python -m examples.llama_http --model-config model.json --model-url http://127.0.0.1:8792 --port 8793

The configuration contains model, expected_build, and optional expected_model_path.
No model is downloaded, started, or stopped by this example.
"""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import ipaddress
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse

from decilist import Decilist
from decilist.llama_cpp import LlamaCppProvider
from decilist.server import create_app


def model_origin(value: str) -> str:
    message = "model-url must be a numeric loopback HTTP(S) origin without credentials, path, query, or fragment"
    try:
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
        if (value != value.strip() or parsed.scheme not in {"http", "https"} or not address.is_loopback
                or (isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None)
                or parsed.username or parsed.password or parsed.path not in {"", "/"} or "?" in value or "#" in value
                or (port is not None and not 1 <= port <= 65535)):
            raise ValueError(message)
    except (TypeError, ValueError) as exc:
        raise ValueError(message) from exc
    return value.rstrip("/")


def _unique_fields(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Model config contains a duplicate field")
        result[key] = value
    return result


def _configuration(config: dict) -> dict:
    allowed = {"model", "expected_build", "expected_model_path"}
    if (not isinstance(config, dict) or set(config) - allowed or not {"model", "expected_build"} <= config.keys()
            or any(not isinstance(value, str) or not value.strip() for value in config.values())):
        raise ValueError("Model config needs model and expected_build, with optional expected_model_path; all values must be nonempty strings")
    return dict(config)


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as source:
        data = source.read(65537)
    if len(data) > 65536:
        raise ValueError("Model config exceeds 64 KiB")
    return _configuration(json.loads(data, object_pairs_hook=_unique_fields))


def create_llama_app(config: dict, *, model_url: str = "http://127.0.0.1:8792"):
    """Construct an app; provider readiness is checked only during explicit startup."""
    config = _configuration(config)
    provider = LlamaCppProvider(base_url=model_origin(model_url), **config)
    app = create_app(Decilist([provider]), provider="llama-cpp", include_ui=False)
    app.state.llama_provider = provider
    app.state.provider_ready = False

    @asynccontextmanager
    async def lifespan(_app):
        try:
            app.state.provider_metadata = await provider.ready()
            app.state.provider_ready = True
            yield
        finally:
            app.state.provider_ready = False
            # This closes our provider's HTTP connection pool, never the model server.
            await provider.aclose()

    app.router.lifespan_context = lifespan

    @app.middleware("http")
    async def require_ready(request, call_next):
        if not app.state.provider_ready:
            return JSONResponse({"service":"decilist", "provider":"llama-cpp", "ready":False, "status":"not_ready"}, status_code=503)
        return await call_next(request)

    return app


def _url_argument(value: str) -> str:
    try:
        return model_origin(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _port_argument(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer from 1 through 65535") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be from 1 through 65535")
    return port


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--model-url", type=_url_argument, default="http://127.0.0.1:8792")
    parser.add_argument("--port", type=_port_argument, default=8793)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.model_config)
        import uvicorn
        app = create_llama_app(config, model_url=args.model_url)
        uvicorn.run(app, host="127.0.0.1", port=args.port, proxy_headers=False, lifespan="on")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Local model HTTP example: {exc}", file=sys.stderr)
        return 2
    except ImportError:
        print("Local model HTTP example: install the Decilist server dependencies in this Python environment.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
