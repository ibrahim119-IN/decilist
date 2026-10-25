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
LAB_ROOT = REPO_ROOT.parents[1] / "work" / "triage-v2-lab"
BASE_REVISION = "83c9fbcf2a090b2862ec0be740e2d2d5c8e72a30"
CATALOG = Path(__file__).with_name("triage_v2_cases.json")
FREEZE = Path(__file__).with_name("triage_v2_freeze.json")
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
'''

TESTS = {
    'strict_schema': '\nfrom pydantic import ValidationError\nfrom decilist import Policy\n@pytest.mark.parametrize("payload", [{"local_only":"false"}, {"timeout_ms":"15"}, {"min_confidence":"0.8"}])\ndef test_policy_rejects_coerced_string_fields(payload):\n    with pytest.raises(ValidationError):\n        Policy(**payload)\n',
    'boolean_probability': '\nfrom decilist import Choose\nfrom decilist.contracts import validate_probabilities\n@pytest.mark.parametrize("probabilities", [{"a":True,"b":False}, {"a":False,"b":True}, {"a":True,"b":0}])\ndef test_boolean_is_not_a_numeric_probability(probabilities):\n    question = Choose(state="Observed request", instructions="Select", options={"a":"A","b":"B"})\n    with pytest.raises(ValueError, match="finite numbers"):\n        validate_probabilities(question, probabilities)\n',
    'text_bound': '\nfrom decilist import Predict\n@pytest.mark.parametrize("character", ["a", "é", "🙂"])\ndef test_text_accepts_its_inclusive_character_limit(character):\n    state = character * 65536\n    question = Predict(state=state, instructions="Ready?")\n    assert question.state == state\n',
    'portable_uri': '\nfrom urllib.parse import quote\nfrom decilist.triage_evidence import prepare_evidence\n@pytest.mark.parametrize("name", ["notes one.py", "café.py", "module δ.py"])\ndef test_portable_uri_resolves_percent_encoded_source(name):\n    relative = ".triage/runtime-files/" + name\n    target = CASE_ROOT / relative\n    target.parent.mkdir(parents=True, exist_ok=True)\n    target.write_text("item = 7\\nassert item == 8\\n", encoding="utf-8")\n    uri = "file://[PROJECT]/" + quote(relative)\n    evidence = prepare_evidence(f\'File "{uri}", line 2\\nAssertionError: observed assertion\', CASE_ROOT)\n    assert len(evidence["references"]) == 1\n    assert evidence["references"][0]["path"] == relative\n    assert evidence["references"][0]["line"] == 2\n',
    'parent_segments': '\nfrom decilist.triage_evidence import _source_path\n@pytest.mark.parametrize("suffix", ["sub/../visible.py", "sub/./../visible.py", "sub/dir/../../visible.py"])\ndef test_untrusted_parent_segments_are_rejected_before_source_read(suffix):\n    root = CASE_ROOT / ".triage/runtime-files"\n    (root / "sub/dir").mkdir(parents=True, exist_ok=True)\n    (root / "visible.py").write_text("answer = 9\\n", encoding="utf-8")\n    relative = ".triage/runtime-files/" + suffix\n    assert _source_path(relative, CASE_ROOT) is None, "lexical parent segments must be rejected at the source-path boundary"\n',
    'cancellation': '\nfrom decilist import Choose, Decilist, FunctionProvider\n@pytest.mark.parametrize("mode", ["raise", "future", "task"])\nasync def test_caller_cancellation_propagates_without_fallback(mode):\n    calls = []\n    class CancelledProvider:\n        name, model, locality = "cancelled", "fixture", "local"\n        async def infer(self, question):\n            if mode == "future":\n                future = asyncio.get_running_loop().create_future()\n                future.cancel()\n                return await future\n            if mode == "task":\n                asyncio.current_task().cancel()\n                await asyncio.sleep(0)\n            raise asyncio.CancelledError()\n    def fallback(question):\n        calls.append(True)\n        return {"a":1,"b":0}\n    question = Choose(state="Observed request", instructions="Select", options={"a":"A","b":"B"})\n    with pytest.raises(asyncio.CancelledError):\n        await Decilist([CancelledProvider(), FunctionProvider(fallback)]).decide(question)\n    assert calls == []\n',
    'timeout_units': '\nfrom decilist import Choose, Decilist, Policy\n@pytest.mark.parametrize("milliseconds", [1, 2, 3])\nasync def test_millisecond_timeout_expires_before_slow_provider(milliseconds):\n    class SlowProvider:\n        name, model, locality = "slow", "fixture", "local"\n        async def infer(self, question):\n            await asyncio.sleep(.04)\n            return {"a":1,"b":0}\n    question = Choose(state="Observed request", instructions="Select", options={"a":"A","b":"B"})\n    result = await Decilist([SlowProvider()], Policy(timeout_ms=milliseconds)).decide(question)\n    assert result.status == "abstained"\n    assert result.attempts[0].outcome == "timeout"\n',
    'remote_block': '\nfrom decilist import Choose, Decilist\n@pytest.mark.parametrize("locality", ["remote", "cloud", "unknown"])\nasync def test_nonlocal_provider_is_not_called_under_local_only(locality):\n    calls = []\n    class Provider:\n        name, model = "configured", "fixture"\n        async def infer(self, question):\n            calls.append(True)\n            return {"a":1,"b":0}\n    provider = Provider()\n    provider.locality = locality\n    question = Choose(state="Observed request", instructions="Select", options={"a":"A","b":"B"})\n    result = await Decilist([provider]).decide(question)\n    assert result.attempts[0].outcome == "blocked"\n    assert calls == []\n',
    'httpx_username': '\nfrom urllib.parse import quote\n@pytest.mark.parametrize("username", ["name@example", "café", "用戶"])\ndef test_url_username_decodes_percent_escaped_text(username):\n    url = httpx.URL("https://" + quote(username, safe="") + ":password@example.com/path")\n    assert url.username == username\n    assert url.userinfo != username.encode("utf-8")\n',
    'httpx_host': '\nimport idna\n@pytest.mark.parametrize("host", ["müller.de", "中国.icom.museum", "bücher.de"])\ndef test_url_host_decodes_internationalized_domain(host):\n    ascii_host = idna.encode(host).decode("ascii")\n    url = httpx.URL("https://" + ascii_host + "/")\n    assert url.host == host\n    assert url.raw_host == ascii_host.encode("ascii")\n',
    'httpx_raw_path': '\n@pytest.mark.parametrize("query", ["a=one", "a=1&b=2", "term=caf%C3%A9"])\ndef test_raw_request_target_keeps_query_separator(query):\n    url = httpx.URL("https://example.com/pa%20th?" + query)\n    assert url.raw_path == ("/pa%20th?" + query).encode("ascii")\n    assert url.query == query.encode("ascii")\n',
    'httpx_fragment': '\nfrom urllib.parse import quote\n@pytest.mark.parametrize("fragment", ["part one", "café", "章節"])\ndef test_url_fragment_decodes_text_without_changing_path(fragment):\n    url = httpx.URL("https://example.com/part#" + quote(fragment, safe=""))\n    assert url.fragment == fragment\n    assert url.path == "/part"\n',
    'trial_a_one': '\nfrom decilist import Choose\n@pytest.mark.parametrize("prefix", ["choice", "選項", "étiquette"])\ndef test_valid_choice_count_at_public_upper_boundary(prefix):\n    options = {f"{prefix}{index}": f"Option {index}" for index in range(64)}\n    question = Choose(state="Observed request", instructions="Select", options=options)\n    assert len(question.options) == 64\n',
    'trial_a_two': '\nfrom decilist import Score\n@pytest.mark.parametrize("prefix", ["level", "級別", "étape"])\ndef test_valid_level_count_at_public_upper_boundary(prefix):\n    levels = [f"{prefix}{index}" for index in range(10)]\n    question = Score(state="Observed request", instructions="Rate", levels=levels)\n    assert question.levels == levels\n',
    'trial_b_one': '\n@pytest.mark.parametrize("values", [["first","second"], ["é","後","last"], ["0","1","2","3"]])\ndef test_mapping_get_preserves_documented_duplicate_value(values):\n    params = httpx.QueryParams([("key", value) for value in values])\n    assert params.get("key") == values[0]\n    assert params.get_list("key") == values\n',
    'trial_b_two': '\n@pytest.mark.parametrize("values", [["alpha","beta"], ["甲","é","omega"], ["one","two","three","four"]])\ndef test_mapping_index_preserves_documented_duplicate_value(values):\n    params = httpx.QueryParams([("key", value) for value in values])\n    assert params["key"] == values[0]\n    assert params.get_list("key") == values\n',
    'ambiguous': '\nfrom decilist import Choose, Decilist\n@pytest.mark.parametrize("locality", ["remote", "cloud", "unknown"])\nasync def test_observed_execution_invariant(locality):\n    class Provider:\n        name, model = "configured", "fixture"\n        async def infer(self, question): return {"a":1,"b":0}\n    provider = Provider()\n    provider.locality = locality\n    result = await Decilist([provider]).decide(Choose(state="Observed", instructions="Select", options={"a":"A","b":"B"}))\n    assert result.attempts[0].outcome == "blocked", "observed execution invariant differed"\n',
    'mixed': '\nfrom decilist import Choose\nfrom decilist.contracts import validate_probabilities\n@pytest.mark.parametrize("probabilities", [{"a":True,"b":False}, {"a":False,"b":True}, {"a":True,"b":0}])\ndef test_boolean_is_not_a_numeric_probability(probabilities):\n    question = Choose(state="Observed request", instructions="Select", options={"a":"A","b":"B"})\n    with pytest.raises(ValueError, match="finite numbers"):\n        validate_probabilities(question, probabilities)\n\nfrom decilist import Choose, Decilist, Policy\n@pytest.mark.parametrize("milliseconds", [1, 2, 3])\nasync def test_millisecond_timeout_expires_before_slow_provider(milliseconds):\n    class SlowProvider:\n        name, model, locality = "slow", "fixture", "local"\n        async def infer(self, question):\n            await asyncio.sleep(.04)\n            return {"a":1,"b":0}\n    question = Choose(state="Observed request", instructions="Select", options={"a":"A","b":"B"})\n    result = await Decilist([SlowProvider()], Policy(timeout_ms=milliseconds)).decide(question)\n    assert result.status == "abstained"\n    assert result.attempts[0].outcome == "timeout"\n',
}

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_catalog() -> dict:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    if catalog["base_revision"] != BASE_REVISION or len(catalog["cases"]) != 19:
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
    return catalog


def get_case(case_id: str) -> dict:
    for case in load_catalog()["cases"]:
        if case["id"] == case_id:
            return case
    raise ValueError("unknown case ID")


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
        raise ValueError("case directories must be strictly inside workspace work/triage-v2-lab and outside the checkout")
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
