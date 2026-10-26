"""Explicit Windows CUDA setup/serve helper for the pinned stronger-local experiment.

Nothing downloads or starts on import. Run download explicitly, then serve from
verified local artifacts. Existing services are never reused or stopped.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from time import perf_counter, sleep
import urllib.error
import urllib.request
import zipfile

MODEL_REPO = "ggml-org/Qwen3-Coder-30B-A3B-Instruct-GGUF"
MODEL_REVISION = "0ca51122012fe4a20dc82988ee58780b008ce4e3"
MODEL_FILE = "Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf"
MODEL_BYTES = 17988419552
MODEL_SHA256 = "f6050b4da22869aa3fb9d94bb3314e9fc5a027d4741c1f97761c387d6b2b2335"
BUILD = "b11889"
COMMIT = "d854d98bae598388ecea1ed84396587e5edc326d"
ALIAS = "qwen3-coder-30b-a3b-q4km"
SERVER_EXE_SHA256 = "049243ee4986362abecbe70b1a2ac1457d01bc4dc6178f3a3a46d9d199f57022"
ARTIFACTS = [
    {"name": "llama-b11889-bin-win-cuda-12.4-x64.zip", "bytes": 279414515,
     "sha256": "ce07031c3e4ab2a98d8b6addf1937b9f60d743208a569d3aad116cd25d6a41bd"},
    {"name": "cudart-llama-bin-win-cuda-12.4-x64.zip", "bytes": 391443627,
     "sha256": "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6"},
]
DLL_NAMES = ("cublas64_12.dll", "cublasLt64_12.dll", "cudart64_12.dll")


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def verify(path, expected_bytes, expected_hash):
    if not path.is_file() or path.stat().st_size != expected_bytes or digest(path) != expected_hash:
        raise RuntimeError(f"Missing or invalid pinned artifact: {path.name}")
    return {"file": path.name, "bytes": expected_bytes, "sha256": expected_hash}


def download_archive(cache, artifact):
    downloads = cache / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    target = downloads / artifact["name"]
    if not target.exists():
        partial = target.with_name(target.name + ".partial")
        for attempt in range(5):
            received = partial.stat().st_size if partial.exists() else 0
            request = urllib.request.Request(f"https://github.com/ggml-org/llama.cpp/releases/download/{BUILD}/{target.name}",
                                             headers={"User-Agent": "Decilist-explicit-local-setup", **({"Range": f"bytes={received}-"} if received else {})})
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    expected_range = f"bytes {received}-{artifact['bytes'] - 1}/{artifact['bytes']}"
                    if received and (response.status != 206 or response.headers.get("Content-Range") != expected_range):
                        raise RuntimeError("Server did not honor the requested resume offset")
                    with partial.open("ab" if received else "wb") as output:
                        while chunk := response.read(1024 * 1024):
                            output.write(chunk)
                            if output.tell() > artifact["bytes"]:
                                raise RuntimeError("Artifact exceeded its pinned size")
                if partial.stat().st_size != artifact["bytes"]:
                    raise RuntimeError("Downloaded archive has an unexpected length")
                partial.replace(target)
                break
            except (OSError, urllib.error.URLError, RuntimeError):
                if attempt == 4:
                    raise
                sleep(min(2 ** attempt, 8))
    record = verify(target, artifact["bytes"], artifact["sha256"])
    runtime = cache / "runtime"
    runtime.mkdir(exist_ok=True)
    with zipfile.ZipFile(target) as archive:
        total = sum(member.file_size for member in archive.infolist())
        for member in archive.infolist():
            (runtime / member.filename).resolve().relative_to(runtime.resolve())
        if shutil.disk_usage(cache).free < total + 3_000_000_000:
            raise RuntimeError("Keep at least3GB free while extracting the runtime")
        archive.extractall(runtime)
    return record


def explicit_download(args):
    if os.name != "nt":
        raise RuntimeError("This pinned helper currently targets Windows x64 CUDA")
    cache = args.cache_dir.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    # Hub configuration is captured at import time.
    os.environ.update(HF_HOME=str(cache / "hf-home"), HF_HUB_DISABLE_TELEMETRY="1",
                      HF_HUB_DISABLE_IMPLICIT_TOKEN="1", HF_XET_HIGH_PERFORMANCE="1")
    os.environ.pop("HF_HUB_OFFLINE", None)
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise RuntimeError("Explicit download requires huggingface_hub in your chosen isolated Python environment; this helper never installs packages automatically") from exc
    # Do not assume interrupted Xet staging files can be reused by a new process.
    # Keep enough space even if the Hub needs a new staging file.
    minimum = 3_000_000_000 if (cache / "model" / MODEL_FILE).exists() else MODEL_BYTES + 3_000_000_000
    if shutil.disk_usage(cache).free < minimum:
        raise RuntimeError("Insufficient free space for the pinned model plus3GB reserve")
    tick = perf_counter()
    path = Path(hf_hub_download(MODEL_REPO, filename=MODEL_FILE, revision=MODEL_REVISION,
                                 local_dir=cache / "model"))
    model = verify(path, MODEL_BYTES, MODEL_SHA256)
    archives = [download_archive(cache, ARTIFACTS[0])]
    if args.cuda_dll_dir is None:
        archives.append(download_archive(cache, ARTIFACTS[1]))
    result = {"checked_at_utc": utc(), "model": {**model, "repo": MODEL_REPO, "revision": MODEL_REVISION,
              "license": "Apache-2.0"}, "runtime": {"tag": BUILD, "commit": COMMIT, "license": "MIT", "archives": archives},
              "initial_download_and_verification_seconds": perf_counter() - tick,
              "cuda_dependencies": "User-supplied existing DLL directory" if args.cuda_dll_dir else "Pinned official CUDA12.4 archive; NVIDIA terms also apply"}
    (cache / "explicit-setup-manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


def serve(args):
    if os.name != "nt":
        raise RuntimeError("This pinned helper currently targets Windows x64 CUDA")
    cache = args.cache_dir.resolve(strict=True)
    model = cache / "model" / MODEL_FILE
    tick = perf_counter()
    verified_model = verify(model, MODEL_BYTES, MODEL_SHA256)
    verification_seconds = perf_counter() - tick
    runtime = args.runtime_dir.resolve(strict=True) if args.runtime_dir else cache / "runtime"
    executable = runtime / "llama-server.exe"
    if not executable.is_file():
        raise RuntimeError("llama-server.exe is missing; run explicit download first")
    executable_hash = digest(executable)
    if executable_hash != SERVER_EXE_SHA256:
        raise RuntimeError("llama-server.exe does not match the pinned official archive member")
    dll_dir = args.cuda_dll_dir.resolve(strict=True) if args.cuda_dll_dir else runtime
    dlls = [{"file": name, "bytes": (dll_dir / name).stat().st_size, "sha256": digest(dll_dir / name)} for name in DLL_NAMES]
    # Bind-check only; never probe, reuse, or stop an unrelated listener.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", args.port))
    command = [str(executable), "--model", str(model), "--alias", ALIAS,
               "--host", "127.0.0.1", "--port", str(args.port), "--ctx-size", str(args.context),
               "--parallel", "1", "--n-gpu-layers", "99", "--split-mode", "none", "--main-gpu", "0",
               "--fit", "off", "--batch-size", "512", "--ubatch-size", "128", "--flash-attn", "on",
               "--jinja", "--offline", "--no-context-shift", "--no-webui"]
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=str(args.gpu), CUDA_DEVICE_ORDER="PCI_BUS_ID",
               CUDA_CACHE_PATH=str(cache / "cuda-cache"), HF_HOME=str(cache / "hf-home"),
               HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    (cache / "cuda-cache").mkdir(exist_ok=True)
    # Isolate this new local runtime from inherited RPC, model, or API options.
    for name in list(env):
        if name.startswith("LLAMA_ARG_") or name in {"LLAMA_API_KEY", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"}:
            env.pop(name)
    env["PATH"] = str(dll_dir) + os.pathsep + str(runtime) + os.pathsep + env.get("PATH", "")
    version = subprocess.run([str(executable), "--version"], env=env, capture_output=True, text=True, timeout=30, check=True)
    actual_version = version.stdout + version.stderr
    if "build 11889" not in actual_version or COMMIT[:9] not in actual_version:
        raise RuntimeError("The executable does not report the pinned llama.cpp build")
    devices = subprocess.run([str(executable), "--list-devices"], env=env, capture_output=True, text=True, timeout=30, check=True)
    if "CUDA0:" not in devices.stdout:
        raise RuntimeError("The requested isolated CUDA device is unavailable")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    log_path = cache / "llama-server.log"
    process = None
    manifest = {"started_at_utc": utc(), "model": verified_model, "model_revision": MODEL_REVISION,
                "runtime_tag": BUILD, "runtime_commit": COMMIT, "alias": ALIAS,
                "runtime_version_probe": actual_version.strip(), "runtime_executable_sha256": executable_hash,
                "runtime_device_probe": devices.stdout.strip(),
                "device": {"physical_gpu_requested": args.gpu, "visible_device": "CUDA0"},
                "context_tokens": args.context, "parallel_slots": 1, "host": "127.0.0.1", "port": args.port,
                "cuda_dlls": dlls, "pre_start_full_model_verification_seconds": verification_seconds,
                "limits": ["Local server flags do not constitute an OS firewall or packet audit.",
                           "CUDA DLL hashes are recorded; user-supplied dependency origin must be reviewed separately."]}
    try:
        with log_path.open("w", encoding="utf-8") as log:
            tick = perf_counter()
            process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            manifest["pid"] = process.pid
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Owned llama-server exited before readiness; inspect its local log")
                try:
                    with opener.open(f"http://127.0.0.1:{args.port}/health", timeout=2) as response:
                        if response.status == 200:
                            manifest["health"] = json.load(response)
                            if manifest["health"].get("status") != "ok":
                                raise RuntimeError("Runtime health did not report status=ok")
                            break
                except (OSError, urllib.error.URLError):
                    pass
                if perf_counter() - tick > 600:
                    raise TimeoutError("Owned llama-server did not become ready in600seconds")
                sleep(.5)
            manifest["cached_process_start_to_health_seconds"] = perf_counter() - tick
            for endpoint in ("props", "v1/models"):
                with opener.open(f"http://127.0.0.1:{args.port}/{endpoint}", timeout=5) as response:
                    manifest[endpoint] = json.load(response)
            props = manifest["props"]
            build_info = props.get("build_info")
            model_path = props.get("model_path")
            if (props.get("model_alias") != ALIAS or not isinstance(build_info, str)
                    or "11889" not in build_info or COMMIT[:9] not in build_info
                    or not isinstance(model_path, str) or Path(model_path).resolve() != model.resolve()):
                raise RuntimeError("Runtime properties do not identify the expected pinned model/build")
            models = manifest["v1/models"].get("data")
            if not isinstance(models, list) or len(models) != 1 or models[0].get("id") != ALIAS:
                raise RuntimeError("Runtime model list does not identify the expected sole alias")
            # The earlier bind-check has a race. Corroborate that the listener
            # belongs to our still-live child, rather than trusting arbitrary HTTP200.
            owners = subprocess.run(["powershell", "-NoProfile", "-Command",
                f"@(Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort {args.port} -State Listen -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess) | ConvertTo-Json -Compress"],
                capture_output=True, text=True, check=True, timeout=15)
            listener_pids = json.loads(owners.stdout)
            if isinstance(listener_pids, int):
                listener_pids = [listener_pids]
            if process.poll() is not None or listener_pids != [process.pid]:
                raise RuntimeError("The expected owned runtime process does not own the loopback listener")
            manifest["owned_listener_pid_verified"] = process.pid
            model_config = cache / "decilist-model.json"
            with model_config.open("w", encoding="utf-8", newline="\n") as config:
                json.dump({"model": ALIAS, "expected_build": props["build_info"]}, config, indent=2)
                config.write("\n")
            manifest["model_config_file"] = model_config.name
            manifest["ready_at_utc"] = utc()
            (cache / "server-ready-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"ready": True, "url": f"http://127.0.0.1:{args.port}", "alias": ALIAS,
                              "pid": process.pid, "model_config": str(model_config),
                              "cached_start_seconds": manifest["cached_process_start_to_health_seconds"]}), flush=True)
            return process.wait()
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("download", "serve"))
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--cuda-dll-dir", type=Path)
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--port", type=int, default=8792)
    parser.add_argument("--context", type=int, default=8192)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.context <= 32768 or args.gpu < 0:
        parser.error("Invalid port, context, or GPU index")
    return explicit_download(args) if args.mode == "download" else serve(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
