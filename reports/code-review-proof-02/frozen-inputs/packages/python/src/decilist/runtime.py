"""Explicit local runtime commands; importing this module starts nothing."""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import threading
from time import monotonic, sleep
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import httpx

SCHEMA_VERSION = "1"
DEFAULT_URL = "http://127.0.0.1:8765"


def _device_arg(value: str) -> str:
    if value not in ("cuda", "cpu") and re.fullmatch(r"cuda:[0-9]+", value) is None:
        raise argparse.ArgumentTypeError("use cpu, cuda, or an indexed CUDA device such as cuda:1")
    return value


def runtime_version() -> str:
    try:
        return version("decilist")
    except PackageNotFoundError:
        return "0.1.0a1"


class RuntimeConnectionError(RuntimeError):
    """The endpoint is unavailable, is not ready, or rejected the request."""


class RuntimeCompatibilityError(RuntimeConnectionError):
    """The endpoint is not a compatible Decilist runtime."""


def validate_runtime_health(payload: Any, *, provider: str | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("service") != "decilist":
        raise RuntimeCompatibilityError("This endpoint is not a Decilist runtime. Check the URL or port.")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeCompatibilityError("Incompatible Decilist schema version. Install matching SDK and runtime packages.")
    if not isinstance(payload.get("runtime_version"), str) or not payload["runtime_version"]:
        raise RuntimeCompatibilityError("The Decilist runtime did not report its version. Update the runtime.")
    if payload.get("ready") is not True or payload.get("status") != "ok":
        raise RuntimeConnectionError("The Decilist runtime is not ready yet. Wait for model loading to finish.")
    if provider is not None and payload.get("provider") != provider:
        raise RuntimeCompatibilityError(f"A different Decilist provider is already using this port; expected {provider}. Choose another port.")
    return payload


def validate_base_url(base_url: str) -> str:
    url = httpx.URL(base_url)
    if url.scheme not in ("http", "https") or not url.host or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
        raise ValueError("base_url must be an HTTP(S) origin without credentials, path, query, or fragment")
    return str(url).rstrip("/")


def probe_runtime(base_url: str = DEFAULT_URL, *, timeout: float = 1, provider: str | None = None) -> dict[str, Any]:
    base_url = validate_base_url(base_url)
    try:
        with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            response = client.get(base_url + "/health")
        if response.status_code != 200:
            raise RuntimeCompatibilityError(f"The endpoint returned HTTP {response.status_code} instead of Decilist health. Check the URL or port.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeCompatibilityError("The endpoint did not return Decilist health JSON. Check the URL or port.") from exc
        return validate_runtime_health(payload, provider=provider)
    except httpx.HTTPError as exc:
        raise RuntimeConnectionError(f"Cannot reach Decilist at {base_url}. Start it with: decilist runtime start --provider demo") from exc


def _load_provider(args: argparse.Namespace):
    if args.provider == "demo":
        from .demo import make_demo_engine
        return make_demo_engine()
    if not args.allow_model_code:
        raise RuntimeConnectionError("Liquid needs reviewed vendor code. Add --allow-model-code after reviewing the pinned model repository.")
    from .engine import Decilist
    from .contracts import Policy
    from .providers import LiquidLocalProvider
    try:
        provider = LiquidLocalProvider.load(allow_model_code=True, device=args.device, offline=True)
    except ImportError as exc:
        raise RuntimeConnectionError("Liquid dependencies are missing. Install decilist[server,liquid] in this Python environment.") from exc
    except Exception as exc:
        raise RuntimeConnectionError("Could not load the cached Liquid model. Run explicit setup first, check the device, and inspect the model environment. No fallback provider was started.") from exc
    return Decilist([provider], Policy(timeout_ms=30000))


def _command(command: list[str], base_url: str) -> int:
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise RuntimeConnectionError("runtime run requires an application command after --, for example: -- node app.mjs")
    try:
        return subprocess.run(command, shell=False, env={**os.environ, "DECILIST_URL": base_url}).returncode
    except OSError as exc:
        raise RuntimeConnectionError("Could not start the application command. Use an installed executable and pass its arguments after --.") from exc


def _start(args: argparse.Namespace, command: list[str] | None = None) -> int:
    # Reserve the address before model loading. An occupied port is never taken over.
    base_url = f"http://{args.host}:{args.port}"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind((args.host, args.port))
        except OSError as exc:
            try:
                health = probe_runtime(base_url, provider=args.provider)
            except RuntimeConnectionError as health_error:
                raise RuntimeConnectionError(f"Port {args.port} is occupied and cannot be reused: {health_error} No process was stopped.") from exc
            print(f"Reusing ready Decilist {health['runtime_version']} ({args.provider}) at {base_url}. No process was started.")
            return 0 if command is None else _command(command, base_url)
        try:
            import uvicorn
            from .server import create_app
        except ImportError as exc:
            raise RuntimeConnectionError("Runtime dependencies are missing. Install decilist[server] in this Python environment.") from exc
        print(f"Loading {args.provider} provider. Liquid startup uses cached files only.", flush=True)
        engine = _load_provider(args)
        app = create_app(engine, provider=args.provider)
        print(f"Starting Decilist at {base_url}. Ctrl+C stops this runtime.", flush=True)
        config = uvicorn.Config(app, host=args.host, port=args.port, proxy_headers=False, log_level="info", timeout_graceful_shutdown=5)
        server = uvicorn.Server(config)
        if command is None:
            server.run(sockets=[sock])
            return 0 if server.started else 1
        # The HTTP server stays in this process: no detached runtime, PID file, or process takeover.
        worker = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, name="decilist-http")
        worker.start()
        try:
            deadline = monotonic() + 10
            while True:
                try:
                    probe_runtime(base_url, timeout=.25, provider=args.provider)
                    break
                except RuntimeCompatibilityError:
                    raise
                except RuntimeConnectionError:
                    if not worker.is_alive() or monotonic() >= deadline:
                        raise RuntimeConnectionError("The owned runtime failed to become ready; the application command was not started.")
                    sleep(.05)
            return _command(command, base_url)
        finally:
            server.should_exit = True
            worker.join(timeout=7)
            if worker.is_alive():
                print("Decilist is waiting for active local work to finish before shutdown.", file=sys.stderr)
                worker.join()
    finally:
        sock.close()


def _setup(args: argparse.Namespace) -> int:
    from .providers import LiquidLocalProvider
    if not args.allow_model_code or not args.allow_download:
        raise RuntimeConnectionError("Setup downloads model assets and runs pinned vendor Python code. Review https://huggingface.co/LiquidAI/d1-3B and its terms, then pass --allow-download --allow-model-code.")
    print(f"Model: {LiquidLocalProvider.model}\nPinned revision: {LiquidLocalProvider.revision}\nTerms and vendor code: https://huggingface.co/LiquidAI/d1-3B", flush=True)
    try:
        from huggingface_hub import model_info
        info = model_info(LiquidLocalProvider.model, revision=LiquidLocalProvider.revision, files_metadata=True)
        sizes = [item.size for item in info.siblings or []]
        total = sum(size for size in sizes if isinstance(size, int))
        print(f"Repository assets: {total / 1024**3:.2f} GiB (cached files are reused; Hugging Face shows transfer progress)." if total and all(isinstance(size, int) for size in sizes) else "Download size unavailable; Hugging Face shows transfer progress. Cached files are reused.", flush=True)
        LiquidLocalProvider.load(allow_model_code=True, device=args.device, offline=False)
    except ImportError as exc:
        raise RuntimeConnectionError("Liquid dependencies are missing. Install decilist[server,liquid] in this Python environment.") from exc
    except Exception as exc:
        raise RuntimeConnectionError("Liquid setup failed. Cached downloads are retained; check network access, model terms, dependencies, and device, then rerun the same command.") from exc
    print("Liquid is cached and loaded successfully. Start with: decilist runtime start --provider liquid --allow-model-code --device " + args.device)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="decilist", description="Explicit local Decilist runtime; no account or saved definitions required.")
    parser.add_argument("--version", action="version", version=runtime_version())
    commands = parser.add_subparsers(dest="command", required=True)
    runtime = commands.add_parser("runtime", help="Set up, start, or inspect the local runtime")
    actions = runtime.add_subparsers(dest="action", required=True)
    for action, help_text in [("start", "Start a foreground runtime, or reuse a matching ready runtime"), ("run", "Start or reuse a runtime, run an application, then stop only the owned runtime")]:
        start = actions.add_parser(action, help=help_text)
        start.add_argument("--provider", choices=["demo", "liquid"], required=True)
        start.add_argument("--host", choices=["127.0.0.1"], default="127.0.0.1", help="Loopback only")
        start.add_argument("--port", type=int, default=8765)
        start.add_argument("--device", default="cuda", type=_device_arg, help="cpu, cuda, or an indexed CUDA device such as cuda:1")
        start.add_argument("--allow-model-code", action="store_true")
        if action == "run":
            start.add_argument("application", nargs=argparse.REMAINDER)
    status = actions.add_parser("status", help="Check runtime identity, readiness, and contract version")
    status.add_argument("--url", default=DEFAULT_URL)
    setup = actions.add_parser("setup", help="Explicit, resumable Liquid download and load check")
    setup.add_argument("provider", choices=["liquid"])
    setup.add_argument("--device", default="cuda", type=_device_arg, help="cpu, cuda, or an indexed CUDA device such as cuda:1")
    setup.add_argument("--allow-download", action="store_true")
    setup.add_argument("--allow-model-code", action="store_true")
    args = parser.parse_args(argv)
    if args.action in ("start", "run") and not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        if args.action == "start":
            return _start(args)
        if args.action == "run":
            if not args.application or args.application == ["--"]:
                parser.error("runtime run requires an application command after --")
            return _start(args, args.application)
        if args.action == "setup":
            return _setup(args)
        print(json.dumps(probe_runtime(args.url), indent=2))
        return 0
    except (RuntimeConnectionError, ValueError) as exc:
        print(f"Decilist: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
