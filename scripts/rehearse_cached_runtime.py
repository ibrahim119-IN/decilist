"""One cached Liquid HTTP/app rehearsal under a Python loopback-only socket guard.

This is not an OS air gap, native-code traffic audit, browser run, or human study.
The supplied cache must already contain the reviewed pinned model. No setup or
download is attempted. Only the server created by this process is shut down.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import importlib.metadata
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
from time import perf_counter
import traceback
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
REVISION = "051bcc464b01b9f92942b364d9586b0ef5912432"


def utc():
    return datetime.now(timezone.utc).isoformat()


class LoopbackGuard:
    def __init__(self):
        self.phase = "workload"
        self.blocked = []
        self.allowed_counts = {}

    def address(self, operation, address, family=socket.AF_INET):
        host = address[0] if isinstance(address, tuple) else address
        port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
        if isinstance(host, bytes):
            host = host.decode("ascii", errors="replace")
        if isinstance(host, str) and host.lower().rstrip(".") == "localhost":
            numeric = "::1" if family == socket.AF_INET6 else "127.0.0.1"
        else:
            try:
                ip = ipaddress.ip_address(host.split("%", 1)[0] if isinstance(host, str) else host)
                allowed = ip.is_loopback or bool(getattr(ip, "ipv4_mapped", None) and ip.ipv4_mapped.is_loopback)
                numeric = str(host) if allowed else None
            except (ValueError, TypeError):
                numeric = None
        if numeric is None:
            self.blocked.append({"phase": self.phase, "operation": operation,
                                 "host": str(host)[:256], "port": port, "blocked": True})
            raise OSError("Non-loopback Python connection/DNS operation blocked by rehearsal")
        self.allowed_counts[operation] = self.allowed_counts.get(operation, 0) + 1
        return (numeric, *address[1:]) if isinstance(address, tuple) else numeric

    @contextmanager
    def installed(self):
        originals = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)
        guard = self

        def connect(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                address = guard.address("socket.connect", address, sock.family)
            return originals[0](sock, address)

        def connect_ex(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                address = guard.address("socket.connect_ex", address, sock.family)
            return originals[1](sock, address)

        def create_connection(address, *args, **kwargs):
            return originals[2](guard.address("socket.create_connection", address), *args, **kwargs)

        def getaddrinfo(host, port, family=0, *args, **kwargs):
            host = guard.address("socket.getaddrinfo", (host, port), family)[0]
            return originals[3](host, port, family, *args, **kwargs)

        try:
            socket.socket.connect = connect
            socket.socket.connect_ex = connect_ex
            socket.create_connection = create_connection
            socket.getaddrinfo = getaddrinfo
            yield
        finally:
            socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo = originals

    def self_test(self):
        self.phase = "guard_self_test"
        checks = []
        for operation in ("connect", "connect_ex", "create_connection", "getaddrinfo"):
            try:
                if operation in {"connect", "connect_ex"}:
                    with socket.socket() as probe:
                        getattr(probe, operation)(("203.0.113.1", 443))
                elif operation == "create_connection":
                    socket.create_connection(("203.0.113.1", 443), timeout=.1)
                else:
                    socket.getaddrinfo("rehearsal-blocked.invalid", 443)
            except OSError:
                checks.append({"operation": operation, "blocked": True})
            else:
                raise RuntimeError("Rehearsal guard failed its controlled negative check")
        self.phase = "workload"
        return checks


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.dependencies = []
        self.inline_scripts = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            if "src" in attrs:
                self.dependencies.append(attrs["src"])
            else:
                self.inline_scripts += 1
        elif tag == "link" and "stylesheet" in attrs.get("rel", "").split():
            self.dependencies.append(attrs.get("href", ""))
        elif tag in {"img", "source", "video", "audio"} and "src" in attrs:
            self.dependencies.append(attrs["src"])
        elif tag == "base" and "href" in attrs:
            self.dependencies.append(attrs["href"])


def same_origin_reference(value):
    parsed = urlsplit(value)
    return bool(value) and not parsed.scheme and not parsed.netloc and not value.startswith("//")


def command_output(command):
    try:
        return subprocess.run(command, cwd=REPO, text=True, capture_output=True, check=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


async def rehearse(args, report, guard):
    import httpx
    import uvicorn
    from decilist import Decilist, LiquidLocalProvider, Policy
    from decilist.contracts import Decision, question_adapter, validate_decision
    from decilist.server import create_app

    owned_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server = None
    serving = None
    port = None
    try:
        # Keep the socket reserved until uvicorn serves it; never reuse another server.
        try:
            owned_socket.bind(("127.0.0.1", args.port))
        except OSError as exc:
            if exc.errno not in {98, 10048}:
                raise
            owned_socket.bind(("127.0.0.1", 0))
        owned_socket.listen(128)
        owned_socket.setblocking(False)
        port = owned_socket.getsockname()[1]
        report["server"] = {"host": "127.0.0.1", "requested_port": args.port, "port": port,
                            "pid": os.getpid(), "ownership": "new socket and runtime in this rehearsal process"}
        report["guard_self_test"] = guard.self_test()
        tick = perf_counter()
        provider = LiquidLocalProvider.load(allow_model_code=True, device=args.device, offline=True)
        report["cached_provider_load_ms"] = (perf_counter() - tick) * 1000
        if provider.revision != REVISION:
            raise RuntimeError("Provider revision differs from the reviewed pinned revision")
        report["provider"] = {"name": provider.name, "model": provider.model, "revision": provider.revision,
                              "device": args.device, "attention_backend": provider.attention_backend}
        import torch
        report["runtime"] = {"python": platform.python_version(), "platform": platform.system(),
                             "packages": {name: importlib.metadata.version(name) for name in
                                          ("decilist", "torch", "torchvision", "transformers", "huggingface-hub", "httpx", "uvicorn")},
                             "cuda_runtime": torch.version.cuda, "gpu": torch.cuda.get_device_name(args.device)}
        torch.cuda.reset_peak_memory_stats(args.device)
        engine = Decilist([provider], Policy(local_only=True, min_confidence=.7, timeout_ms=30000))
        app = create_app(engine, provider="liquid", local_only=True, include_ui=True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                             access_log=False, log_level="warning", loop="asyncio"))
        tick = perf_counter()
        serving = asyncio.create_task(server.serve(sockets=[owned_socket]))
        while not server.started:
            if serving.done():
                await serving
                raise RuntimeError("Owned runtime exited before startup")
            if perf_counter() - tick > 30:
                raise TimeoutError("Owned runtime did not start in 30 seconds")
            await asyncio.sleep(.01)
        report["server_startup_ms"] = (perf_counter() - tick) * 1000
        responses = []
        report["http_requests"] = responses
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=120, trust_env=False) as client:
            async def request(method, path, payload=None):
                tick = perf_counter()
                try:
                    response = await client.request(method, path, json=payload) if payload is not None else await client.request(method, path)
                except Exception as exc:
                    responses.append({"method": method, "path": path, "status_code": None,
                                      "http_elapsed_ms": (perf_counter() - tick) * 1000,
                                      "error_type": type(exc).__name__})
                    raise
                record = {"method": method, "path": path, "status_code": response.status_code,
                          "http_elapsed_ms": (perf_counter() - tick) * 1000,
                          "content_type": response.headers.get("content-type"),
                          "content_security_policy": response.headers.get("content-security-policy")}
                responses.append(record)
                response.raise_for_status()
                return response, record

            health, _ = await request("GET", "/health")
            report["health"] = health.json()
            if report["health"].get("provider") != "liquid" or not any(row.get("revision") == REVISION for row in report["health"].get("providers", [])):
                raise RuntimeError("Owned HTTP health did not identify the pinned learned provider")
            assets = {}
            dependencies = []
            inline_scripts = 0
            for path in ("/", "/demo", "/app.js", "/demo.js", "/style.css"):
                response, record = await request("GET", path)
                assets[path] = {"bytes": len(response.content), "sha256": hashlib.sha256(response.content).hexdigest()}
                if path in {"/", "/demo"}:
                    parsed = Assets()
                    parsed.feed(response.text)
                    dependencies += parsed.dependencies
                    inline_scripts += parsed.inline_scripts
                elif path.endswith(".css"):
                    dependencies += re.findall(r"url\(\s*['\"]?([^)'\"\s]+)", response.text)
                    dependencies += re.findall(r"@import\s+['\"]([^'\"]+)", response.text)
                else:
                    dependencies += re.findall(r"fetch\(\s*['\"]([^'\"]+)", response.text)
            required = {"default-src": ["'self'"], "script-src": ["'self'"],
                        "style-src": ["'self'"], "connect-src": ["'self'"], "frame-ancestors": ["'none'"]}
            csp_checks = []
            for response in responses:
                directives = {part.split()[0]: part.split()[1:] for part in (response["content_security_policy"] or "").split(";") if part.split()}
                csp_checks.append(all(directives.get(name) == values for name, values in required.items()))
            report["ui_asset_check"] = {"served_assets": assets, "literal_dependency_urls": sorted(set(dependencies)),
                                        "all_literal_dependencies_same_origin": all(same_origin_reference(value) for value in dependencies),
                                        "inline_script_count": inline_scripts, "self_only_csp_on_health_and_assets": all(csp_checks),
                                        "scope": "Actual HTTP bodies/headers and literal HTML/CSS/fetch references; no browser JavaScript execution or layout verification"}
            questions = [
                {"kind": "choose", "state": "Please summarize the attached document in three bullet points.",
                 "instructions": "Which action is explicitly requested?",
                 "options": {"summarize": "Summarize a document", "translate": "Translate text", "compare": "Compare documents"}},
                {"kind": "predict", "state": "Please translate this paragraph from English into Arabic.",
                 "instructions": "Does the request explicitly specify a target language?"},
                {"kind": "score", "state": "The request includes an action, an input document, and a requested output format.",
                 "instructions": "Rate completeness of the request using only the stated fields.",
                 "levels": ["No fields are present", "One or two fields are present", "All three fields are present"]},
            ]
            report["operations"] = []
            for question in questions:
                response, record = await request("POST", "/v1/decisions", {"question": question,
                    "policy": {"local_only": True, "min_confidence": .7, "timeout_ms": 30000}})
                decision = Decision.model_validate(response.json())
                validate_decision(question_adapter.validate_python(question), decision)
                report["operations"].append({"kind": question["kind"], "http_elapsed_ms": record["http_elapsed_ms"],
                    "question": question, "decision": decision.model_dump(mode="json")})
            report["app_flow"] = []
            async def app_step(name, text, clarification="", parent=None):
                payload = {"request": text, "clarification": clarification}
                response, record = await request("POST", "/v1/demo/request", payload)
                result = response.json()
                if result["outcome"] not in {"proceed", "clarify", "review", "unavailable"}:
                    raise RuntimeError("Unknown observed app outcome")
                if (result["outcome"] == "proceed") != (result["draft"] is not None):
                    raise RuntimeError("Observed app draft does not match its final outcome")
                if result["outcome"] == "proceed" and result["draft"] != {
                    "request": text, "clarification": clarification,
                    "source": "Project update", "destination": "Private drafts"}:
                    raise RuntimeError("Observed app draft differs from the supplied request/context")
                report["app_flow"].append({"id": name, "parent_step": parent, "input": payload,
                    "http_elapsed_ms": record["http_elapsed_ms"], "observed": result})
                return result
            await app_step("complete_request", "Prepare a short summary of the project update.")
            missing = await app_step("missing_format", "Prepare something from this document.")
            if missing["outcome"] == "clarify":
                await app_step("clarification_continuation", "Prepare something from this document.", "A short checklist.", "missing_format")
            else:
                report["clarification_continuation_skipped"] = "Initial observed outcome was not clarify; no UI continuation was fabricated."
            await app_step("review_request", "Email a summary to the team.")
            report["http_requests"] = responses
        report["gpu_memory"] = {"allocated_bytes": torch.cuda.memory_allocated(args.device),
                                "peak_allocated_bytes": torch.cuda.max_memory_allocated(args.device),
                                "scope": "This process's PyTorch allocator; excludes other processes/native allocations"}
        report["observed_app_outcomes"] = sorted({row["observed"]["outcome"] for row in report["app_flow"]})
        report["success"] = (report["ui_asset_check"]["all_literal_dependencies_same_origin"]
            and report["ui_asset_check"]["self_only_csp_on_health_and_assets"] and inline_scripts == 0
            and all(not any(attempt["outcome"] in {"error", "timeout", "invalid_output", "blocked"}
                            for attempt in row["decision"]["attempts"]) for row in report["operations"])
            and all(row["observed"]["outcome"] != "unavailable" for row in report["app_flow"]))
    finally:
        if server is not None:
            server.should_exit = True
        if serving is not None:
            try:
                await asyncio.wait_for(asyncio.shield(serving), timeout=15)
            except (TimeoutError, asyncio.CancelledError):
                serving.cancel()
                await asyncio.gather(serving, return_exceptions=True)
            except Exception as exc:
                report["shutdown_error_type"] = type(exc).__name__
                report["success"] = False
        owned_socket.close()
        listener_activity = [listener.is_serving() for listener in getattr(server, "servers", [])]
        closed = owned_socket.fileno() == -1 and not any(listener_activity)
        result = None
        if port is not None:
            with socket.socket() as probe:
                probe.settimeout(1)
                result = probe.connect_ex(("127.0.0.1", port))
        report["cleanup"] = {"own_server_task_finished": serving is None or serving.done(),
                             "own_listener_closed": closed, "post_shutdown_connect_ex": result,
                             "own_asyncio_listener_activity": listener_activity,
                             "closure_check": "Owned listener is_serving flags and closed socket descriptor; connect_ex is diagnostic only",
                             "original_runtime_touched": False}
        report["success"] = bool(report.get("success")) and closed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hf-home", type=Path, required=True, help="Existing explicit local Hugging Face cache")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--output", type=Path, default=REPO / "reports/triage-v2-cached-runtime-rehearsal-2026-10-26.json")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; preserve the earlier observation and choose a new path")
    cache = args.hf_home.resolve(strict=True)
    snapshot = cache / "hub/models--LiquidAI--d1-3B/snapshots" / REVISION
    if not (snapshot / "model.safetensors").is_file():
        parser.error("Pinned cached model is missing; no setup or download will be attempted")
    os.environ.update(HF_HOME=str(cache), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1", HF_HUB_DISABLE_UPDATE_CHECK="1")
    report = {"schema_version": 1, "kind": "agent-run-cached-liquid-http-app-rehearsal",
              "started_at_utc": utc(), "success": False, "human_usability_time": None,
              "source_revision": command_output(["git", "rev-parse", "HEAD"]),
              "source_dirty": bool(command_output(["git", "status", "--porcelain"])),
              "gpu_preflight_csv": command_output(["nvidia-smi", "--query-gpu=index,name,driver_version,memory.total,memory.free,utilization.gpu", "--format=csv,noheader"]),
              "cache": {"pinned_snapshot_present": True, "weights_bytes": (snapshot / "model.safetensors").stat().st_size,
                        "download_attempted_by_script": False},
              "environment": {name: os.environ[name] for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "HF_HUB_DISABLE_UPDATE_CHECK")},
              "concurrency_note": "Coordinated agent trials were reported complete before this rehearsal; other background machine activity was not controlled.",
              "reproduction": {"working_directory": "repository root", "command": ["../../work/.venv-liquid/Scripts/python.exe",
                  "scripts/rehearse_cached_runtime.py", "--hf-home", "../../work/model-cache", "--device", args.device,
                  "--port", str(args.port), "--output", "reports/triage-v2-cached-runtime-rehearsal-2026-10-26.json"]},
              "limits": ["Cached load excludes initial model/library downloads and installation.",
                         "Python connect/connect_ex/create_connection/getaddrinfo wrappers only; native extensions, separate processes, OS interfaces and packet capture are not audited.",
                         "On Windows, asyncio Proactor native ConnectEx can bypass these Python wrappers; actual HTTP destinations in this run are explicit loopback literals.",
                         "AF_UNIX local IPC is allowed; IP connections and name resolution require loopback. localhost is rewritten to a loopback literal.",
                         "Guard self-tests are deliberately blocked attempts, reported separately from actual model/HTTP workload.",
                         "HTTP and static literal-asset checks only; no browser offline run, visual layout verification, OS air gap, or human walkthrough.",
                         "Shared machine: background CPU/network/GPU activity was not controlled; timings are observations, not controlled benchmarks or human productivity measures."]}
    guard = LoopbackGuard()
    tick = perf_counter()
    try:
        with guard.installed():
            asyncio.run(rehearse(args, report, guard))
    except Exception:
        from decilist.triage_evidence import _project_pattern, _sanitize
        report["error"] = _sanitize(traceback.format_exc(), _project_pattern(REPO))[0]
    finally:
        report["network_guard"] = {"blocked_operations": guard.blocked,
            "blocked_self_test_operations": sum(row["phase"] == "guard_self_test" for row in guard.blocked),
            "blocked_workload_operations": sum(row["phase"] == "workload" for row in guard.blocked),
            "allowed_loopback_operations_by_wrapper": guard.allowed_counts,
            "all_blocked_before_original_socket_or_dns_function": True}
        report["success"] = bool(report["success"]) and report["network_guard"]["blocked_workload_operations"] == 0
        report["elapsed_ms"] = (perf_counter() - tick) * 1000
        report["finished_at_utc"] = utc()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    print(json.dumps({"success": report["success"], "output": args.output.name,
                      "cached_load_ms": report.get("cached_provider_load_ms"),
                      "observed_app_outcomes": report.get("observed_app_outcomes"),
                      "blocked_workload_operations": report["network_guard"]["blocked_workload_operations"],
                      "cleanup": report.get("cleanup")}))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
