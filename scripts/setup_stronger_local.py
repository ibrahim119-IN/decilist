"""Explicit Windows CUDA setup/serve helper for pinned local model profiles.

Nothing downloads or starts on import. Run download explicitly, then serve from
verified local artifacts. Existing services are never reused or stopped.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
from contextlib import contextmanager
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
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
DEFAULT_PROFILE = ALIAS
CLOSED_THINKING_PREFIX = "<|im_start|>assistant\n<think>\n\n</think>\n\n"


@dataclass(frozen=True)
class ModelProfile:
    key: str
    repo: str
    revision: str
    file: str
    bytes: int
    sha256: str
    alias: str
    context: int
    port: int
    batch: int
    ubatch: int
    gpus: tuple[int, ...]
    tensor_split: tuple[float, ...] | None
    minimum_free_gpu_mib: tuple[int, ...] | None
    reasoning: str | None = None
    publisher_repo: str | None = None
    publisher_revision: str | None = None
    license: str = "Apache-2.0"


PROFILES = {
    DEFAULT_PROFILE: ModelProfile(DEFAULT_PROFILE, MODEL_REPO, MODEL_REVISION, MODEL_FILE,
        MODEL_BYTES, MODEL_SHA256, ALIAS, 8192, 8792, 512, 128, (0,), None, None),
    "qwen36-27b-q8": ModelProfile("qwen36-27b-q8", "ggml-org/Qwen3.6-27B-GGUF",
        "1132d03976d88e30a09ab0ddbf1531d5e1b46e7c", "Qwen3.6-27B-Q8_0.gguf", 28595762464,
        "73f8260284708ed78ae266df672288b6ad1f2c73ec7ffeb7514b5cecdba646c9", "qwen36-27b-q8",
        16384, 8794, 256, 64, (0, 1), (3.0, 2.0), (20480, 14336), "off",
        "Qwen/Qwen3.6-27B", "6a9e13bd6fc8f0983b9b99948120bc37f49c13e9"),
}


def integer_list(value):
    parts = value.split(",")
    if not parts or any(not part.isascii() or not part.isdecimal() for part in parts):
        raise argparse.ArgumentTypeError("Use comma-separated nonnegative integer GPU IDs or MiB values")
    return tuple(int(part) for part in parts)


def weights(value):
    try:
        result = tuple(float(part) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use comma-separated positive finite tensor weights") from exc
    if not result or any(not math.isfinite(part) or part <= 0 for part in result):
        raise argparse.ArgumentTypeError("Tensor weights must be positive finite numbers")
    return result


def selected_profile(args):
    return PROFILES[getattr(args, "profile", DEFAULT_PROFILE)]


def settings(args):
    profile = selected_profile(args)
    chosen_gpus = (args.gpu,) if args.gpu is not None else args.gpus or profile.gpus
    if not 1 <= len(chosen_gpus) <= 16 or len(set(chosen_gpus)) != len(chosen_gpus) or any(gpu < 0 for gpu in chosen_gpus):
        raise ValueError("Choose 1 to 16 distinct nonnegative physical GPU indices")
    split = args.tensor_split
    if split is None:
        split = profile.tensor_split if len(chosen_gpus) > 1 else None
    if len(chosen_gpus) > 1 and (split is None or len(split) != len(chosen_gpus)):
        raise ValueError("Multi-GPU selection needs one tensor-split weight per visible GPU")
    if split is not None and len(split) != len(chosen_gpus):
        raise ValueError("Tensor split must match the selected GPU count")
    if not math.isfinite(args.host_reserve_gb) or not 0 <= args.host_reserve_gb <= 512:
        raise ValueError("Host reserve must be a finite 0..512 decimal GB value")
    result = {"gpus": chosen_gpus, "tensor_split": split,
              "port": profile.port if args.port is None else args.port,
              "context": profile.context if args.context is None else args.context,
              "batch": profile.batch if args.batch_size is None else args.batch_size,
              "ubatch": profile.ubatch if args.ubatch_size is None else args.ubatch_size,
              "gpu_layers": args.gpu_layers, "main_gpu": args.main_gpu,
              "host_reserve_bytes": int(args.host_reserve_gb * 1_000_000_000)}
    if not 1 <= result["port"] <= 65535 or not 1 <= result["context"] <= 32768:
        raise ValueError("Port must be 1..65535 and context must be 1..32768")
    if not 1 <= result["batch"] <= 8192 or not 1 <= result["ubatch"] <= result["batch"]:
        raise ValueError("Batch must be 1..8192 and ubatch must be positive and no larger than batch")
    if not 0 <= result["gpu_layers"] <= 999 or not 0 <= result["main_gpu"] < len(chosen_gpus):
        raise ValueError("GPU layers must be 0..999; main-gpu is an index in the selected visible GPU list")
    required = args.min_free_gpu_mib
    audited_layout = (len(chosen_gpus) == len(profile.gpus) and split == profile.tensor_split
                      and result["gpu_layers"] == 99 and result["main_gpu"] == 0 and result["context"] <= profile.context
                      and result["batch"] <= profile.batch and result["ubatch"] <= profile.ubatch)
    if required is None and profile.minimum_free_gpu_mib is not None and audited_layout:
        required = profile.minimum_free_gpu_mib
    if required is None and (profile.minimum_free_gpu_mib is not None or len(chosen_gpus) > 1):
        raise ValueError("Changed resource layout needs explicit --min-free-gpu-mib budgets, one per selected GPU")
    if required is not None and (len(required) != len(chosen_gpus) or any(value <= 0 for value in required)):
        raise ValueError("GPU memory budgets must be positive MiB values matching the selected GPU count")
    result["minimum_free_gpu_mib"] = required
    result["resource_budget_source"] = "explicit user budget" if args.min_free_gpu_mib is not None else "audited conservative plan" if required else "legacy single-GPU compatibility"
    return result


def storage_paths(cache):
    paths = {"HF_HOME": str(cache / "hf-home"), "HF_HUB_CACHE": str(cache / "hf-home/hub"),
            "HF_ASSETS_CACHE": str(cache / "hf-home/assets"), "HF_XET_CACHE": str(cache / "hf-home/xet"),
            "XDG_CACHE_HOME": str(cache / "cache"), "CUDA_CACHE_PATH": str(cache / "cuda-cache"),
            "TEMP": str(cache / "tmp"), "TMP": str(cache / "tmp"), "TMPDIR": str(cache / "tmp")}
    for path in paths.values():
        Path(path).resolve().relative_to(cache.resolve())
    return paths


def validate_owned_paths(cache):
    # Refuse existing links/junctions that would send setup writes outside the
    # selected output root. This does not prevent concurrent local filesystem changes.
    storage_paths(cache)
    for name in ("model", "downloads", "runtime", "launches"):
        (cache / name).resolve().relative_to(cache.resolve())


def validate_download_paths(profile, cache):
    validate_owned_paths(cache)
    root = cache.resolve()
    local = cache / "model"
    # Hub local_dir can write the artifact and its own metadata independently.
    # Check the leaves as well as the parent directories before calling Hub.
    metadata_root = local / ".cache/huggingface"
    for path in (local / profile.file, local / ".cache", metadata_root):
        path.resolve().relative_to(root)
    pending, seen = [metadata_root], set()
    while pending:
        directory = pending.pop()
        resolved = directory.resolve()
        resolved.relative_to(root)
        if resolved in seen or not directory.exists():
            continue
        seen.add(resolved)
        if not directory.is_dir():
            raise ValueError("Hub local metadata must be a directory inside --cache-dir")
        for path in directory.iterdir():
            path.resolve().relative_to(root)
            if path.is_dir():
                pending.append(path)


@contextmanager
def download_storage(cache):
    paths = storage_paths(cache)
    switches = {"HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
                "HF_HUB_DISABLE_SHARED_BLOBS": "1", "HF_XET_HIGH_PERFORMANCE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    removed = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")
    selected = {**paths, **switches}
    previous = {key: os.environ.get(key) for key in (*selected, *removed)}
    old_temp = tempfile.tempdir
    for path in set(paths.values()):
        Path(path).mkdir(parents=True, exist_ok=True)
    os.environ.update(selected)
    for key in removed:
        os.environ.pop(key, None)
    tempfile.tempdir = paths["TEMP"]
    try:
        yield
    finally:
        tempfile.tempdir = old_temp
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def hub_download(profile, cache):
    validate_download_paths(profile, cache)
    # A reused import can capture an earlier native Xet cache. A fresh CLI process
    # is required instead of silently writing to a previous output directory.
    if "hf_xet" in sys.modules:
        raise RuntimeError("Run download in a fresh CLI process so native cache configuration stays inside --cache-dir")
    constants = sys.modules.get("huggingface_hub.constants")
    if constants is not None:
        for key in ("HF_HOME", "HF_HUB_CACHE", "HF_ASSETS_CACHE", "HF_XET_CACHE"):
            value = getattr(constants, key, None)
            if value is not None and Path(value).resolve() != Path(storage_paths(cache)[key]).resolve():
                raise RuntimeError("A prior Hub import captured another cache; use a fresh CLI process")
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise RuntimeError("Explicit download requires huggingface_hub in your isolated Python environment; no packages are installed automatically") from exc
    return Path(hf_hub_download(profile.repo, filename=profile.file, revision=profile.revision,
                               local_dir=cache / "model", cache_dir=cache / "hf-home/hub"))


def launch_command(args, profile, chosen, cache):
    runtime = args.runtime_dir.resolve() if args.runtime_dir else cache / "runtime"
    command = [str(runtime / "llama-server.exe"), "--model", str(cache / "model" / profile.file), "--alias", profile.alias,
        "--host", "127.0.0.1", "--port", str(chosen["port"]), "--ctx-size", str(chosen["context"]),
        "--parallel", "1", "--n-gpu-layers", str(chosen["gpu_layers"]),
        "--split-mode", "layer" if len(chosen["gpus"]) > 1 else "none", "--main-gpu", str(chosen["main_gpu"]),
        "--fit", "off", "--batch-size", str(chosen["batch"]), "--ubatch-size", str(chosen["ubatch"]),
        "--flash-attn", "on", "--jinja", "--offline", "--no-context-shift", "--no-webui"]
    if chosen["tensor_split"] is not None and len(chosen["gpus"]) > 1:
        command += ["--tensor-split", ",".join(f"{value:g}" for value in chosen["tensor_split"])]
    if profile.reasoning is not None:
        command += ["--cache-type-k", "f16", "--cache-type-v", "f16", "--reasoning", profile.reasoning, "--reasoning-format", "none"]
    return command


def child_environment(args, chosen, cache):
    runtime = args.runtime_dir.resolve() if args.runtime_dir else cache / "runtime"
    dll_dir = args.cuda_dll_dir.resolve() if args.cuda_dll_dir else runtime
    selected = {**storage_paths(cache), "CUDA_VISIBLE_DEVICES": ",".join(map(str, chosen["gpus"])),
                "CUDA_DEVICE_ORDER": "PCI_BUS_ID", "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    env = dict(os.environ)
    for name in list(env):
        if name.upper().startswith("LLAMA_ARG_") or name.upper() in {"LLAMA_API_KEY", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"}:
            env.pop(name)
    env.update(selected)
    env["PATH"] = str(dll_dir) + os.pathsep + str(runtime) + os.pathsep + env.get("PATH", "")
    return env, selected


def plan(args):
    profile, chosen = selected_profile(args), settings(args)
    cache = args.cache_dir.resolve()
    _env, selected = child_environment(args, chosen, cache)
    return {"profile": asdict(profile), "settings": chosen, "cache_root": str(cache),
            "argv": launch_command(args, profile, chosen, cache), "selected_environment": selected,
            "runtime_actions": 0, "network_actions": 0,
            "compatibility": "Qwen3.6 loading, actual default non-thinking prefix and GPU fit are pending; this is a plan" if profile.reasoning else "Existing Coder default arguments preserved; this plan performs no compatibility test"}


def utc():
    return datetime.now(timezone.utc).isoformat()


def require_windows():
    if os.name != "nt":
        raise RuntimeError("This pinned helper currently targets Windows x64 CUDA")


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
    require_windows()
    cache = args.cache_dir.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    profile = selected_profile(args)
    validate_download_paths(profile, cache)
    # Do not assume interrupted Xet staging files can be reused by a new process.
    # Keep enough space even if the Hub needs a new staging file.
    minimum = 3_000_000_000 if (cache / "model" / profile.file).exists() else profile.bytes + 3_000_000_000
    if shutil.disk_usage(cache).free < minimum:
        raise RuntimeError("Insufficient free space for the pinned model plus3GB reserve")
    tick = perf_counter()
    with download_storage(cache):
        path = hub_download(profile, cache)
        path.resolve().relative_to(cache)
        model = verify(path, profile.bytes, profile.sha256)
        archives = [download_archive(cache, ARTIFACTS[0])]
        if args.cuda_dll_dir is None:
            archives.append(download_archive(cache, ARTIFACTS[1]))
    result = {"checked_at_utc": utc(), "profile": profile.key,
              "model": {**model, "repo": profile.repo, "revision": profile.revision,
              "license": profile.license, "publisher_repo": profile.publisher_repo, "publisher_revision": profile.publisher_revision},
              "runtime": {"tag": BUILD, "commit": COMMIT, "license": "MIT", "archives": archives},
              "initial_download_and_verification_seconds": perf_counter() - tick,
              "cache_and_temp_paths": storage_paths(cache), "runtime_actions": 0, "generation_requests": 0,
              "cuda_dependencies": "User-supplied existing DLL directory" if args.cuda_dll_dir else "Pinned official CUDA12.4 archive; NVIDIA terms also apply"}
    name = "explicit-setup-manifest.json" if profile.key == DEFAULT_PROFILE else f"explicit-setup-manifest-{profile.key}.json"
    (cache / name).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


def collect_resources():
    result = subprocess.run(["nvidia-smi", "--query-gpu=index,uuid,name,memory.total,memory.free", "--format=csv,noheader,nounits"],
                            capture_output=True, text=True, check=True, timeout=20)
    gpus = []
    for row in csv.reader(result.stdout.splitlines()):
        index, uuid, name, total, free = (value.strip() for value in row)
        if not uuid.startswith("GPU-"):
            raise RuntimeError("GPU UUID identity is missing; no model was launched")
        gpus.append({"index": int(index), "uuid": uuid, "name": name, "total_mib": int(total), "free_mib": int(free)})
    ram = subprocess.run(["powershell", "-NoProfile", "-Command",
        "$m=Get-CimInstance Win32_OperatingSystem; @{available_bytes=[long]$m.FreePhysicalMemory*1024} | ConvertTo-Json -Compress"],
        capture_output=True, text=True, check=True, timeout=20)
    return {"checked_at_utc": utc(), "gpus": gpus, "host_ram": json.loads(ram.stdout),
            "scope": "Whole-device allocations and available host RAM, not model-owned memory"}


def require_resources(profile, chosen, measured, cache_free):
    budgets = chosen["minimum_free_gpu_mib"]
    if budgets is None:
        return
    by_index = {row["index"]: row for row in measured["gpus"]}
    if any(index not in by_index or by_index[index]["free_mib"] < minimum
           for index, minimum in zip(chosen["gpus"], budgets)):
        raise RuntimeError("Measured GPU headroom does not meet the declared per-GPU budget; existing services were not stopped")
    if measured["host_ram"]["available_bytes"] < profile.bytes + chosen["host_reserve_bytes"]:
        raise RuntimeError("Available host RAM does not meet model bytes plus the declared reserve")
    if cache_free < 3_000_000_000:
        raise RuntimeError("Keep at least 3 GB free in --cache-dir before loading")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise RuntimeError("The numeric loopback runtime attempted a redirect")


def metadata(opener, url, path, body=None):
    raw = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    request = urllib.request.Request(url + path, data=raw, headers={} if raw is None else {"Content-Type": "application/json"})
    with opener.open(request, timeout=10) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise RuntimeError("Runtime metadata/template exceeded its bounded size")
    result = json.loads(data)
    if not isinstance(result, dict):
        raise RuntimeError("Runtime metadata/template must be a JSON object")
    return result


def nonthinking_proof(opener, url):
    body = {"messages": [{"role": "user", "content": "Neutral template-format check."}]}
    result = metadata(opener, url, "/apply-template", body)
    prompt = result.get("prompt")
    if not isinstance(prompt, str) or not prompt.endswith(CLOSED_THINKING_PREFIX):
        raise RuntimeError("Default template did not expose the expected closed non-thinking prefix")
    return {"request": body, "prompt": prompt, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "passed": True, "generation_requests": 0, "note": "Default no-override template proof; not a reasoning-quality result"}


def loaded_cuda_modules(pid, dll_dir, configured):
    command = f"@((Get-Process -Id {pid}).Modules | Where-Object {{ $_.ModuleName -in @('cublas64_12.dll','cublasLt64_12.dll','cudart64_12.dll') }} | Select-Object ModuleName,FileName) | ConvertTo-Json -Compress"
    result = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, check=True, timeout=20)
    rows = json.loads(result.stdout)
    if isinstance(rows, dict):
        rows = [rows]
    expected = {row["file"].lower(): row["sha256"] for row in configured}
    if not isinstance(rows, list) or {row.get("ModuleName", "").lower() for row in rows} != set(expected):
        raise RuntimeError("The owned child did not report all configured CUDA dependency modules")
    for row in rows:
        path = Path(row["FileName"]).resolve()
        if path != (dll_dir / row["ModuleName"]).resolve() or digest(path) != expected[row["ModuleName"].lower()]:
            raise RuntimeError("A CUDA module loaded outside the configured directory or changed hash")
        row["sha256"] = expected[row["ModuleName"].lower()]
    return rows


def serve(args):
    require_windows()
    cache = args.cache_dir.resolve(strict=True)
    validate_owned_paths(cache)
    profile, chosen = selected_profile(args), settings(args)
    # Refuse the chosen occupied address before hashes, hardware probes or Popen.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", chosen["port"]))
    measured = collect_resources() if chosen["minimum_free_gpu_mib"] is not None else None
    if measured is not None:
        resource_file = cache / ("resource-check-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
        resource_record = {"profile": profile.key, "settings": chosen, "measured": measured, "passed": False}
        try:
            require_resources(profile, chosen, measured, shutil.disk_usage(cache).free)
            resource_record["passed"] = True
        finally:
            resource_file.write_text(json.dumps(resource_record, indent=2) + "\n", encoding="utf-8")
    model = cache / "model" / profile.file
    tick = perf_counter()
    verified_model = verify(model, profile.bytes, profile.sha256)
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
    command = launch_command(args, profile, chosen, cache)
    env, selected = child_environment(args, chosen, cache)
    for path in set(storage_paths(cache).values()):
        Path(path).mkdir(parents=True, exist_ok=True)
    if measured is not None:
        by_index = {row["index"]: row for row in measured["gpus"]}
        # UUID selection binds the nvidia-smi resource rows to the actual devices
        # even on systems where CUDA ordinal order differs from driver indices.
        env["CUDA_VISIBLE_DEVICES"] = ",".join(by_index[index]["uuid"] for index in chosen["gpus"])
        selected["CUDA_VISIBLE_DEVICES"] = env["CUDA_VISIBLE_DEVICES"]
    version = subprocess.run([str(executable), "--version"], env=env, capture_output=True, text=True, timeout=30, check=True)
    actual_version = version.stdout + version.stderr
    if "build 11889" not in actual_version or COMMIT[:9] not in actual_version:
        raise RuntimeError("The executable does not report the pinned llama.cpp build")
    devices = subprocess.run([str(executable), "--list-devices"], env=env, capture_output=True, text=True, timeout=30, check=True)
    if any(f"CUDA{index}:" not in devices.stdout for index in range(len(chosen["gpus"]))):
        raise RuntimeError("A requested isolated CUDA device is unavailable")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    url = f"http://127.0.0.1:{chosen['port']}"
    launch = cache if profile.key == DEFAULT_PROFILE else cache / "launches" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + profile.key)
    launch.mkdir(parents=True, exist_ok=True)
    log_path = launch / "llama-server.log"
    process = None
    manifest = {"started_at_utc": utc(), "profile": profile.key, "model": verified_model, "model_revision": profile.revision,
                "runtime_tag": BUILD, "runtime_commit": COMMIT, "alias": profile.alias, "argv": command,
                "runtime_version_probe": actual_version.strip(), "runtime_executable_sha256": executable_hash,
                "runtime_device_probe": devices.stdout.strip(),
                "device": {"physical_gpu_requested": chosen["gpus"][0] if profile.key == DEFAULT_PROFILE and len(chosen["gpus"]) == 1 else chosen["gpus"],
                           "visible_device": "CUDA0" if len(chosen["gpus"]) == 1 else "CUDA0.." + str(len(chosen["gpus"]) - 1),
                           "main_visible_gpu": chosen["main_gpu"]},
                "selected_environment": selected, "resource_budget": chosen, "hardware_before": measured,
                "context_tokens": chosen["context"], "parallel_slots": 1, "host": "127.0.0.1", "port": chosen["port"],
                "reasoning": profile.reasoning or "existing template default", "generation_requests_by_setup": 0,
                "cuda_dlls": dlls, "pre_start_full_model_verification_seconds": verification_seconds,
                "limits": ["Local server flags do not constitute an OS firewall or packet audit.",
                           "CUDA DLL hashes are recorded; user-supplied dependency origin must be reviewed separately."]}
    try:
        if measured is not None:
            fresh = collect_resources()
            manifest["hardware_immediately_before_spawn"] = fresh
            require_resources(profile, chosen, fresh, shutil.disk_usage(cache).free)
            fresh_by_index = {row["index"]: row for row in fresh["gpus"]}
            if any(fresh_by_index[index]["uuid"] != by_index[index]["uuid"] for index in chosen["gpus"]):
                raise RuntimeError("GPU identity changed between resource checks; no model was launched")
        with log_path.open("w", encoding="utf-8") as log:
            tick = perf_counter()
            process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            manifest["pid"] = process.pid
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Owned llama-server exited before readiness; inspect its local log")
                try:
                    with opener.open(url + "/health", timeout=2) as response:
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
                manifest[endpoint] = metadata(opener, url, "/" + endpoint)
            props = manifest["props"]
            build_info = props.get("build_info")
            model_path = props.get("model_path")
            if (props.get("model_alias") != profile.alias or not isinstance(build_info, str)
                    or "11889" not in build_info or COMMIT[:9] not in build_info
                    or not isinstance(model_path, str) or Path(model_path).resolve() != model.resolve()):
                raise RuntimeError("Runtime properties do not identify the expected pinned model/build")
            models = manifest["v1/models"].get("data")
            if not isinstance(models, list) or len(models) != 1 or models[0].get("id") != profile.alias:
                raise RuntimeError("Runtime model list does not identify the expected sole alias")
            if (props.get("default_generation_settings", {}).get("n_ctx") != chosen["context"]
                    or models[0].get("meta", {}).get("n_ctx") != chosen["context"]):
                raise RuntimeError("Actual serving context did not match the declared profile")
            # The earlier bind-check has a race. Corroborate that the listener
            # belongs to our still-live child, rather than trusting arbitrary HTTP200.
            owners = subprocess.run(["powershell", "-NoProfile", "-Command",
                f"@(Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort {chosen['port']} -State Listen -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess) | ConvertTo-Json -Compress"],
                capture_output=True, text=True, check=True, timeout=15)
            listener_pids = json.loads(owners.stdout)
            if isinstance(listener_pids, int):
                listener_pids = [listener_pids]
            if process.poll() is not None or listener_pids != [process.pid]:
                raise RuntimeError("The expected owned runtime process does not own the loopback listener")
            manifest["owned_listener_pid_verified"] = process.pid
            if profile.reasoning == "off":
                manifest["actually_loaded_cuda_modules"] = loaded_cuda_modules(process.pid, dll_dir, dlls)
                manifest["nonthinking_template_proof"] = nonthinking_proof(opener, url)
                manifest["hardware_ready"] = collect_resources()
            config_name = "decilist-model.json" if profile.key == DEFAULT_PROFILE else f"decilist-model-{profile.key}.json"
            model_config = cache / config_name
            with model_config.open("w", encoding="utf-8", newline="\n") as config:
                json.dump({"model": profile.alias, "expected_build": props["build_info"]}, config, indent=2)
                config.write("\n")
            manifest["model_config_file"] = model_config.name
            manifest["ready_at_utc"] = utc()
            (launch / "server-ready-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"ready": True, "url": url, "alias": profile.alias,
                              "pid": process.pid, "model_config": str(model_config),
                              "cached_start_seconds": manifest["cached_process_start_to_health_seconds"]}), flush=True)
            return process.wait()
    except Exception as error:
        (launch / "failure.json").write_text(json.dumps({"failed_at_utc": utc(), "exception_type": type(error).__name__,
            "message": str(error)[:1200], "launch": manifest}, indent=2) + "\n", encoding="utf-8")
        raise
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=30)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "download", "serve"))
    parser.add_argument("--profile", choices=tuple(PROFILES), default=DEFAULT_PROFILE)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--cuda-dll-dir", type=Path)
    gpu = parser.add_mutually_exclusive_group()
    gpu.add_argument("--gpu", type=int, help="Legacy single physical GPU selection")
    gpu.add_argument("--gpus", type=integer_list, help="Selected physical GPU indices, for example 0,1")
    parser.add_argument("--port", type=int)
    parser.add_argument("--context", type=int)
    parser.add_argument("--tensor-split", type=weights)
    parser.add_argument("--gpu-layers", type=int, default=99)
    parser.add_argument("--main-gpu", type=int, default=0, help="Visible-device index after GPU selection")
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--ubatch-size", type=int)
    parser.add_argument("--min-free-gpu-mib", type=integer_list, help="Explicit positive free-memory budget per selected GPU; changing the audited layout requires this")
    parser.add_argument("--host-reserve-gb", type=float, default=8, help="Decimal GB of available host RAM required beyond model bytes for guarded profiles")
    args = parser.parse_args(argv)
    try:
        settings(args)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.mode == "plan":
        print(json.dumps(plan(args), indent=2))
        return 0
    return explicit_download(args) if args.mode == "download" else serve(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
