"""Isolated, executable Decilist fixtures. Never patch the live checkout.

Reference repairs are restored from the pinned Git tree in this source-only lab;
neither repair content nor oracle labels are written into participant manifests.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from time import perf_counter
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = REPO_ROOT.parents[1] / "work" / "triage-v3-lab"
BASE_REVISION = "34d0ce4459ad4b46143f8bb3d0cb4f6e45f277af"
CATALOG = Path(__file__).with_name("triage_v3_cases.json")
FREEZE = Path(__file__).with_name("triage_v3_freeze.json")
SOURCE_PREFIX = "packages/python/src/decilist/"
TEST_PATH = ".triage/test_case.py"
CONFIG_PATH = ".triage/pytest.ini"
MANIFEST_PATH = ".triage/manifest.json"
TEST_ARGS = ["-m", "pytest", "-q", "--tb=short", "-c", CONFIG_PATH,
             "--confcutdir=.triage", "-o", "asyncio_mode=auto", "-p", "no:cacheprovider",
             "-p", "pytest_asyncio.plugin", TEST_PATH]

TEST_PREFIX = '''from pathlib import Path
import asyncio
import json
import sys
import pytest
import decilist
import httpx

CASE_ROOT = Path(__file__).resolve().parents[1]
assert Path(decilist.__file__).resolve().is_relative_to(CASE_ROOT / "packages/python/src"), "must import isolated project source"
assert Path(httpx.__file__).resolve().is_relative_to(CASE_ROOT / "vendor/httpx"), "must import isolated external project source"
HEALTH = {"service":"decilist","schema_version":"1","runtime_version":"0.1.0a1","ready":True,"status":"ok","provider":"demo"}
def response_record():
    return httpx.Response(200,json={"schema_version":"1","request_id":"fixture-request","kind":"choose","status":"accepted","value":"a","probabilities":{"a":1,"b":0},"confidence":1,"provider":"fixture","model":"fixture","latency_ms":0,"cost_usd":None,"attempts":[{"provider":"fixture","outcome":"accepted","latency_ms":0}]})
'''

TESTS = {
    'request_settings': '\nfrom decilist import Client, Policy\n@pytest.mark.parametrize("configured,override", [(75,5), (250,20), (900,30)])\nasync def test_three_request_records(configured, override):\n    records = []\n    def handle(request):\n        if request.url.path == "/health": return httpx.Response(200, json=HEALTH)\n        records.append(json.loads(request.content)["policy"]["timeout_ms"])\n        return response_record()\n    async with Client(policy=Policy(timeout_ms=configured)) as client:\n        await client._http.aclose()\n        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url=client.base_url)\n        await client.choose("first", "Select", {"a":"A","b":"B"})\n        await client.choose("second", "Select", {"a":"A","b":"B"}, policy=Policy(timeout_ms=override))\n        await client.choose("third", "Select", {"a":"A","b":"B"})\n        assert records == [configured, override, configured]\n        assert client.policy.timeout_ms == configured\n',
    'readiness_sequence': '\nfrom decilist import Client\nfrom decilist.runtime import RuntimeConnectionError\n@pytest.mark.parametrize("initial_calls", [1,2,3])\nasync def test_recovery_sequence(initial_calls):\n    paths = []\n    posts = 0\n    def handle(request):\n        nonlocal posts\n        paths.append(request.url.path)\n        if request.url.path == "/health": return httpx.Response(200, json=HEALTH)\n        posts += 1\n        if posts == initial_calls+1: raise httpx.ConnectError("connection interrupted", request=request)\n        return response_record()\n    async with Client() as client:\n        await client._http.aclose()\n        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url=client.base_url)\n        for index in range(initial_calls):\n            await client.choose(str(index), "Select", {"a":"A","b":"B"})\n        with pytest.raises(RuntimeConnectionError):\n            await client.choose("interrupted", "Select", {"a":"A","b":"B"})\n        result = await client.choose("later", "Select", {"a":"A","b":"B"})\n        assert result.value == "a"\n        assert paths[-2:] == ["/health", "/v1/decisions"]\n        assert paths.count("/health") == 2\n',
    'observer_worker_lifetime': '\nimport threading\nfrom decilist import Choose, LiquidLocalProvider\n@pytest.mark.parametrize("pause", [.025,.04,.055])\nasync def test_followup_execution(pause):\n    provider = LiquidLocalProvider(object())\n    started, release = threading.Event(), threading.Event()\n    state_lock = threading.Lock()\n    active, peak, count = 0, 0, 0\n    def worker(question):\n        nonlocal active, peak, count\n        with state_lock:\n            active += 1\n            count += 1\n            peak = max(peak, active)\n        started.set()\n        release.wait(2)\n        with state_lock: active -= 1\n        return {"a":1,"b":0}\n    provider._infer_sync = worker\n    question = Choose(state="Observed", instructions="Select", options={"a":"A","b":"B"})\n    first = asyncio.create_task(provider.infer(question))\n    second = None\n    try:\n        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 1), 2)\n        first.cancel()\n        with pytest.raises(asyncio.CancelledError): await first\n        second = asyncio.create_task(provider.infer(question))\n        await asyncio.sleep(pause)\n        with state_lock: observed = count\n    finally:\n        release.set()\n        if second is not None: await asyncio.wait_for(second, 2)\n        while provider._running: await asyncio.sleep(.005)\n    assert observed == 1\n    assert count == 2\n    assert peak == 1\n',
    'request_trace_isolation': '\nfrom decilist import Choose, Decilist, FunctionProvider\n@pytest.mark.parametrize("names", [("first","second"),("local-one","local-two"),("alpha","beta")])\nasync def test_observed_attempt_traces(names):\n    def primary(question):\n        if question.state == "one": return {"a":.2,"b":.2}\n        if question.state == "two": return {"a":1,"b":0}\n        return {"a":.5,"b":.5}\n    engine = Decilist([FunctionProvider(primary,name=names[0]),FunctionProvider(lambda _: {"a":0,"b":1},name=names[1])])\n    results=[]\n    for state in ("one","two","three"):\n        results.append(await engine.decide(Choose(state=state,instructions="Select",options={"a":"A","b":"B"})))\n    assert [result.value for result in results] == ["b","a","b"]\n    traces=[[(attempt.provider,attempt.outcome) for attempt in result.attempts] for result in results]\n    assert traces == [[(names[0],"invalid_output"),(names[1],"accepted")],[(names[0],"accepted")],[(names[0],"low_confidence"),(names[1],"accepted")]]\n',
    'query_merge_pipeline': '\n@pytest.mark.parametrize("key", ["tag","項目","x y"])\ndef test_query_pipeline_result(key):\n    original = httpx.URL("https://example.test/route",params=[(key,"old"),(key,"older"),("keep","yes")])\n    transformed = original.copy_merge_params({key:["new-one","new-two"],"added":"ok"}).copy_add_param(key,"tail")\n    assert transformed.params.get_list(key) == ["new-one","new-two","tail"]\n    assert transformed.params["keep"] == "yes"\n    assert transformed.params["added"] == "ok"\n    assert original.params.get_list(key) == ["old","older"]\n    assert httpx.URL(str(transformed)).params == transformed.params\n',
    'forked_query_state': '\n@pytest.mark.parametrize("key", ["tag","項目","x y"])\ndef test_branch_results(key):\n    parent = httpx.QueryParams([(key,"seed"),(key,"older"),("keep","yes")])\n    left = parent.add(key,"left")\n    right = parent.add(key,"right")\n    assert left.get_list(key) == ["seed","older","left"]\n    assert right.get_list(key) == ["seed","older","right"]\n    assert parent.get_list(key) == ["seed","older"]\n    assert httpx.URL("https://example.test",params=left).params.get_list(key) == ["seed","older","left"]\n',
    'query_presence_normalization': '\n@pytest.mark.parametrize("base,key", [("https://example.test/route","only"),("http://example.test:80/pa%20th","項目"),("https://example.test:443/root/child","x y")])\ndef test_query_removal_result(base, key):\n    original = httpx.URL(base,params={key:"value"})\n    transformed = original.copy_remove_param(key)\n    expected = httpx.URL(base)\n    assert transformed.query == b""\n    assert len(transformed.params) == 0\n    assert transformed.raw_path == expected.raw_path\n    assert transformed == expected\n    assert str(transformed.copy_with(fragment="part")) == str(expected.copy_with(fragment="part"))\n    assert original.params[key] == "value"\n',
    'escaping_roundtrip': '\n@pytest.mark.parametrize("raw", [b"/a%20b/%2Fz", b"/caf%C3%A9/%25done", b"/name%2Fpart/%E7%AB%A0"])\ndef test_url_roundtrip_result(raw):\n    original = httpx.URL("https://example.test/root")\n    transformed = original.copy_with(raw_path=raw)\n    reconstructed = httpx.URL(str(transformed))\n    assert transformed.raw_path == raw\n    assert reconstructed.raw_path == raw\n    assert reconstructed == transformed\n    assert original.raw_path == b"/root"\n    fresh = original.copy_with(path="/fresh café")\n    assert fresh.raw_path == b"/fresh%20caf%C3%A9"\n',
    'reserved_one': '\n@pytest.mark.parametrize("key", ["item","節","x y"])\ndef test_mapping_views(key):\n    parent = httpx.QueryParams([(key,"seed"),(key,"older"),("keep","yes")])\n    left = parent.set(key,"left")\n    right = parent.set(key,"right")\n    assert parent.get_list(key) == ["seed","older"]\n    assert left.get_list(key) == ["left"]\n    assert right.get_list(key) == ["right"]\n    assert left["keep"] == "yes"\n    assert httpx.URL("https://example.test",params=left).params.get_list(key) == ["left"]\n',
    'reserved_two': '\n@pytest.mark.parametrize("key", ["item","節","x y"])\ndef test_mapping_views(key):\n    parent = httpx.QueryParams([(key,"seed"),(key,"older"),("keep","yes"),("other","ok")])\n    left = parent.remove(key)\n    right = parent.remove("keep")\n    assert parent.get_list(key) == ["seed","older"]\n    assert parent["keep"] == "yes"\n    assert left.get_list(key) == []\n    assert left["keep"] == "yes"\n    assert right.get_list(key) == ["seed","older"]\n    assert httpx.URL("https://example.test",params=left).params.get_list(key) == []\n',
    'mixed': '\nfrom decilist import Client, Policy\n@pytest.mark.parametrize("configured,override", [(75,5), (250,20), (900,30)])\nasync def test_three_request_records(configured, override):\n    records = []\n    def handle(request):\n        if request.url.path == "/health": return httpx.Response(200, json=HEALTH)\n        records.append(json.loads(request.content)["policy"]["timeout_ms"])\n        return response_record()\n    async with Client(policy=Policy(timeout_ms=configured)) as client:\n        await client._http.aclose()\n        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url=client.base_url)\n        await client.choose("first", "Select", {"a":"A","b":"B"})\n        await client.choose("second", "Select", {"a":"A","b":"B"}, policy=Policy(timeout_ms=override))\n        await client.choose("third", "Select", {"a":"A","b":"B"})\n        assert records == [configured, override, configured]\n        assert client.policy.timeout_ms == configured\n\n@pytest.mark.parametrize("key", ["tag","項目","x y"])\ndef test_query_pipeline_result(key):\n    original = httpx.URL("https://example.test/route",params=[(key,"old"),(key,"older"),("keep","yes")])\n    transformed = original.copy_merge_params({key:["new-one","new-two"],"added":"ok"}).copy_add_param(key,"tail")\n    assert transformed.params.get_list(key) == ["new-one","new-two","tail"]\n    assert transformed.params["keep"] == "yes"\n    assert transformed.params["added"] == "ok"\n    assert original.params.get_list(key) == ["old","older"]\n    assert httpx.URL(str(transformed)).params == transformed.params\n',
}

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_catalog() -> dict:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    if catalog["base_revision"] != BASE_REVISION or len(catalog["cases"]) != 12:
        raise ValueError("unexpected frozen base or corpus size")
    ids = [case["id"] for case in catalog["cases"]]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case IDs")
    for case in catalog["cases"]:
        if case["test_key"] not in TESTS or not set(case["acceptable_routes"]) <= {"dependency_environment", "contract_validation", "decision_policy", "runtime_lifecycle", "application_logic", "manual_review"}:
            raise ValueError("invalid test or route label")
        if any(not path.startswith((SOURCE_PREFIX, "vendor/httpx/")) or ".." in Path(path).parts for path in case["editable_paths"]):
            raise ValueError("editable files must be scoped production source")
        if not case.get("targeted_inspection_assertion"):
            raise ValueError("every case needs a prespecified targeted inspection assertion")
        if not case.get("root_cause_discriminating_facts") or not case.get("minimal_repair_invariants"):
            raise ValueError("every case needs discriminating facts and repair invariants")
    return catalog


def get_case(case_id: str) -> dict:
    for case in load_catalog()["cases"]:
        if case["id"] == case_id:
            return case
    raise ValueError("unknown case ID")


def trial_case_ids() -> tuple[str, ...]:
    """Neutral trial eligibility; never returns labels, mutations, or repairs."""
    return tuple(case["id"] for case in load_catalog()["cases"] if case["role"] == "trial_regression")


def get_case_role(case_id: str) -> str:
    """Validate eligibility without exposing the private oracle catalog."""
    return get_case(case_id)["role"]


def _git_blob(revision: str, path: str) -> bytes:
    return subprocess.run(["git", "show", f"{revision}:{path}"], cwd=REPO_ROOT,
                          capture_output=True, check=True).stdout


@lru_cache(maxsize=1)
def _base_files() -> dict[str, bytes]:
    archive = subprocess.run(["git", "archive", "--format=tar", BASE_REVISION, "--", SOURCE_PREFIX.rstrip("/")],
                             cwd=REPO_ROOT, capture_output=True, check=True).stdout
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as handle:
        for member in handle:
            if member.isdir():
                continue
            if not member.isfile() or not member.name.startswith(SOURCE_PREFIX) or ".." in Path(member.name).parts:
                raise ValueError("unsafe archived path")
            files[member.name] = handle.extractfile(member).read()
    import httpx
    if httpx.__version__ != "0.28.1":
        raise ValueError("v2 requires the already installed httpx==0.28.1 source; nothing is installed automatically")
    project = load_catalog()["projects"]["httpx"]
    root = Path(httpx.__file__).resolve().parent
    for relative, wanted in project["installed_source_sha256"].items():
        data = (root / relative.removeprefix("vendor/httpx/")).read_bytes()
        if digest(data) != wanted:
            raise ValueError("installed HTTPX source differs from its verified frozen snapshot: " + relative)
        files[relative] = data
    license_data = project["license_text"].encode("utf-8")
    if digest(license_data) != project["license_sha256"]:
        raise ValueError("HTTPX license bytes differ from the verified upstream license")
    files["vendor/LICENSE.httpx.md"] = license_data
    return files


def _python() -> Path:
    path = Path(os.environ.get("DECILIST_TRIAGE_PYTHON", sys.executable)).resolve()
    if not path.is_file():
        raise ValueError("the selected Python executable is unavailable; no environment is installed automatically")
    return path


def _checked_path(path: str | Path, *, new: bool = False) -> Path:
    resolved, allowed = Path(path).resolve(), LAB_ROOT.resolve()
    if resolved == allowed or not resolved.is_relative_to(allowed) or resolved.is_relative_to(REPO_ROOT.resolve()):
        raise ValueError("case directories must be strictly inside workspace work/triage-v3-lab and outside the checkout")
    if new and resolved.exists():
        raise ValueError("destination already exists; use a new isolated directory")
    return resolved


def _registry_path(case_dir: Path) -> Path:
    return LAB_ROOT / ".registry" / (digest(str(case_dir).encode("utf-8")) + ".json")


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def _test_bytes(case: dict) -> bytes:
    return (TEST_PREFIX + TESTS[case["test_key"]]).encode("utf-8")


def _apply_mutation(case_dir: Path, case: dict) -> None:
    for mutation in case["mutations"]:
        path = case_dir / mutation["path"]
        if mutation["kind"] == "historical_blob":
            path.write_bytes(_git_blob(mutation["revision"], mutation["path"]))
        elif mutation["kind"] == "replace":
            text = path.read_text(encoding="utf-8")
            if text.count(mutation["old"]) != 1:
                raise ValueError("scoped mutation must match exactly once: " + case["id"])
            path.write_bytes(text.replace(mutation["old"], mutation["new"]).encode("utf-8"))
        else:
            raise ValueError("unknown mutation")


def prepare_case(case_id: str, dest: str | Path) -> dict:
    """Create a neutral, isolated case. Return no labels or reference repair."""
    case_dir = _checked_path(dest, new=True)
    case = get_case(case_id)
    files = _base_files()
    case_dir.mkdir(parents=True)
    for relative, data in files.items():
        target = case_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (case_dir / TEST_PATH).parent.mkdir()
    (case_dir / TEST_PATH).write_bytes(_test_bytes(case))
    (case_dir / CONFIG_PATH).write_bytes(b"[pytest]\n")
    protected = {path: digest(data) for path, data in files.items() if path not in case["editable_paths"]}
    protected.update({TEST_PATH: digest(_test_bytes(case)), CONFIG_PATH: digest(b"[pytest]\n")})
    _apply_mutation(case_dir, case)
    manifest = {"schema_version": 1, "case_id": case_id, "base_revision": BASE_REVISION, "project": case["project"],
                "editable_paths": case["editable_paths"], "protected_sha256": protected,
                "test_command": [Path(_python()).name, *TEST_ARGS]}
    _write_json(case_dir / MANIFEST_PATH, manifest)
    # Trusted copy is outside the participant directory; editing the case manifest
    # cannot redefine the allowlist or expected test hashes.
    _write_json(_registry_path(case_dir), {"case_id": case_id, "manifest_sha256": digest((case_dir / MANIFEST_PATH).read_bytes()),
        "catalog_sha256": digest(CATALOG.read_bytes()), "protected_sha256": protected})
    return {**manifest, "case_dir": str(case_dir)}


def _integrity(case_dir: Path) -> tuple[bool, list[str], dict]:
    registry_path = _registry_path(case_dir)
    if not registry_path.is_file():
        return False, ["unregistered case directory"], {}
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    case = get_case(registry["case_id"])
    issues = []
    if digest(CATALOG.read_bytes()) != registry["catalog_sha256"]:
        issues.append("source catalog changed after preparation")
    manifest = case_dir / MANIFEST_PATH
    if not manifest.is_file() or digest(manifest.read_bytes()) != registry["manifest_sha256"]:
        issues.append("case manifest changed")
    expected = {path: digest(data) for path, data in _base_files().items() if path not in case["editable_paths"]}
    expected.update({TEST_PATH: digest(_test_bytes(case)), CONFIG_PATH: digest(b"[pytest]\n")})
    if expected != registry["protected_sha256"]:
        issues.append("trusted protected hashes differ from frozen source")
    for relative, wanted in expected.items():
        path = case_dir / relative
        if not path.is_file() or path.is_symlink() or digest(path.read_bytes()) != wanted:
            issues.append("protected file changed: " + relative)
    known = set(_base_files()) | {TEST_PATH, CONFIG_PATH, MANIFEST_PATH}
    for path in case_dir.rglob("*"):
        if path.is_symlink():
            issues.append("symlink is not permitted")
        if path.is_file():
            relative = path.relative_to(case_dir).as_posix()
            if relative.startswith((".triage/runs/", ".triage/runtime-files/")) or "/__pycache__/" in relative:
                continue
            if relative not in known:
                issues.append("unexpected file: " + relative)
    for relative in case["editable_paths"]:
        if not (case_dir / relative).is_file() or (case_dir / relative).is_symlink():
            issues.append("editable production file missing or linked: " + relative)
    return not issues, issues, case


def run_case(case_dir: str | Path) -> dict:
    """Run the protected pytest checks using only the isolated project source."""
    directory = _checked_path(case_dir)
    integrity_ok, issues, case = _integrity(directory)
    if not integrity_ok:
        return {"exit_code": None, "elapsed_ms": 0, "stdout": "", "log_path": None,
                "command": [Path(_python()).name, *TEST_ARGS], "protected_files_ok": False,
                "integrity_issues": issues, "run_sha256": None, "timed_out": False,
                "source_stable": False, "editable_sha256_before": {}, "editable_sha256_after": {}}
    before = {path: digest((directory / path).read_bytes()) for path in case["editable_paths"]}
    environment = dict(os.environ)
    environment.update(PYTHONPATH=os.pathsep.join([str(directory / "vendor"), str(directory / "packages/python/src")]), PYTHONDONTWRITEBYTECODE="1",
                       PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTEST_ADDOPTS="")
    tick = perf_counter()
    try:
        completed = subprocess.run([str(_python()), *TEST_ARGS], cwd=directory, env=environment,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
        exit_code, text, timed_out = completed.returncode, completed.stdout + completed.stderr, False
    except subprocess.TimeoutExpired as error:
        exit_code, timed_out = None, True
        pieces = [value.decode("utf-8", "replace") if isinstance(value, bytes) else value or "" for value in (error.stdout, error.stderr)]
        text = "".join(pieces) + "\nRunner exceeded the 45-second observation bound; process was stopped.\n"
    elapsed = (perf_counter() - tick) * 1000
    post_ok, post_issues, _ = _integrity(directory)
    after = {path: digest((directory / path).read_bytes()) if (directory / path).is_file() else None
             for path in case["editable_paths"]}
    from decilist.triage_evidence import prepare_evidence
    raw_text = text
    prepared = prepare_evidence(text, project=directory)
    text = prepared["log"]
    run_id = uuid4().hex
    relative = f".triage/runs/{run_id}.log"
    target = directory / relative
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(text.encode("utf-8"))
    target.with_suffix(".raw.log").write_bytes(raw_text.encode("utf-8"))
    return {"exit_code": exit_code, "elapsed_ms": elapsed, "stdout": text, "log_path": relative,
            "command": [Path(_python()).name, *TEST_ARGS], "protected_files_ok": post_ok,
            "integrity_issues": post_issues, "run_sha256": digest(target.read_bytes()), "timed_out": timed_out,
            "source_stable": before == after, "editable_sha256_before": before, "editable_sha256_after": after,
            "raw_log_sha256": digest(raw_text.encode("utf-8")), "log_truncated": prepared["truncated"],
            "log_redaction_count": prepared["redaction_count"]}


def validate_repair(case_dir: str | Path) -> dict:
    """A repair passes only the original protected multi-input tests and allowlist."""
    run = run_case(case_dir)
    passed = run["protected_files_ok"] and run["source_stable"] and run["exit_code"] == 0 and not run["timed_out"]
    return {"passed": passed, "integrity_ok": run["protected_files_ok"], "run": run}


def prepare_case_evidence(case_dir: str | Path, run: dict | None = None) -> dict:
    """Shared benchmark/study preparation; never includes catalog/oracle fields."""
    directory = _checked_path(case_dir)
    observed = run if run is not None else run_case(directory)
    if not observed["protected_files_ok"] or observed["run_sha256"] != digest(observed["stdout"].encode("utf-8")):
        raise ValueError("evidence requires an intact observed test run")
    ok, issues, case = _integrity(directory)
    if not ok:
        raise ValueError("case integrity failed before evidence preparation: " + "; ".join(issues))
    current = {path: digest((directory / path).read_bytes()) for path in case["editable_paths"]}
    stable = current == observed["editable_sha256_after"]
    from decilist.triage_evidence import prepare_evidence
    evidence = prepare_evidence(observed["stdout"], project=directory if stable else None, command=observed["command"])
    evidence["exit_code"] = observed["exit_code"]
    if not stable:
        evidence["source_context_omitted"] = "Production source changed after the saved run; excerpts would be stale."
    return evidence


def prepare_machine_evidence(case_dir: str | Path, run: dict | None = None) -> dict:
    """Apply only the frozen control visibility policy; omit all oracle metadata."""
    directory = _checked_path(case_dir)
    observed = run if run is not None else run_case(directory)
    ok, _, case = _integrity(directory)
    if not ok:
        raise ValueError("case integrity failed")
    if case["role"] == "trial_regression":
        raise ValueError("reserved blind trial cases cannot enter machine evaluation")
    if case["evidence_mode"] == "full":
        return prepare_case_evidence(directory, observed)
    from decilist.triage_evidence import prepare_evidence
    if case["evidence_mode"] == "exception_only":
        # A genuine exception type is retained, while its discarded context is
        # explicitly recorded. No synthetic failure message is substituted.
        line = next((line for line in observed["stdout"].splitlines() if "AssertionError" in line), "")
        log = "AssertionError" if line else "Exception details were unavailable."
    else:
        log = "Execution log was truncated before diagnostic details were retained."
    evidence = prepare_evidence(log, command=observed["command"])
    evidence["exit_code"] = observed["exit_code"]
    evidence["truncated"] = True
    return evidence


def _reference_repair(case_dir: Path) -> None:
    """Source-only oracle for corpus verification; never used by diagnose/study."""
    _, _, case = _integrity(case_dir)
    for relative in case["editable_paths"]:
        (case_dir / relative).write_bytes(_base_files()[relative])


def verify_fixture(case_id: str, destination: str | Path) -> dict:
    """Observe unchanged pass, mutation outcome, and reference-repair pass."""
    manifest = prepare_case(case_id, destination)
    directory = Path(manifest["case_dir"])
    case = get_case(case_id)
    _reference_repair(directory)
    baseline = run_case(directory)
    _apply_mutation(directory, case)
    mutation = run_case(directory)
    _reference_repair(directory)
    repaired = validate_repair(directory)
    _apply_mutation(directory, case)
    expected_failure = True
    verified = baseline["exit_code"] == 0 and repaired["passed"] and mutation["protected_files_ok"] and (
        mutation["exit_code"] not in (0, None) if expected_failure else mutation["exit_code"] == 0)
    return {"case_id": case_id, "role": case["role"], "provenance": case["provenance"],
            "verified": bool(verified), "baseline": baseline, "mutation": mutation,
            "reference_repair": repaired, "protected_sha256": manifest["protected_sha256"],
            "editable_mutation_sha256": {path: digest((directory / path).read_bytes()) for path in case["editable_paths"]}}


def verify_freeze() -> dict:
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    if freeze["base_revision"] != BASE_REVISION:
        raise ValueError("frozen baseline mismatch")
    for relative, wanted in freeze["files_sha256"].items():
        if digest((REPO_ROOT / relative).read_bytes()) != wanted:
            raise ValueError("frozen evaluation source changed: " + relative)
    return freeze


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "validate", "verify"))
    parser.add_argument("case_or_directory")
    parser.add_argument("--destination")
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare_case(args.case_or_directory, args.destination)
    elif args.action == "verify":
        result = verify_fixture(args.case_or_directory, args.destination)
    elif args.action == "run":
        result = run_case(args.case_or_directory)
    else:
        result = validate_repair(args.case_or_directory)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
