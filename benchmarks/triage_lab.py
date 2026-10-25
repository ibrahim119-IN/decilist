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
LAB_ROOT = REPO_ROOT.parents[1] / "work" / "triage-lab"
BASE_REVISION = "bf2a1df0a5a0b95dfc923eaa0a67c6fea2f07063"
CATALOG = Path(__file__).with_name("triage_cases.json")
FREEZE = Path(__file__).with_name("triage_freeze.json")
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
from types import ModuleType, SimpleNamespace
import pytest
import decilist

CASE_ROOT = Path(__file__).resolve().parents[1]
assert Path(decilist.__file__).resolve().is_relative_to(CASE_ROOT / "packages/python/src"), "fixture must import isolated source, never the live checkout"
'''

TESTS = {
    "numeric_order": '''
from decilist import Choose, Decilist, Policy, UniformProvider
@pytest.mark.parametrize("keys,expected", [(["10", "2"], "2"), (["8", "3", "1"], "1"), (["z", "12", "4"], "4")])
async def test_numeric_labels_keep_cross_language_tie_order(keys, expected):
    question = Choose(state="Observed input", instructions="Choose", options={key: "Allowed option" for key in keys})
    result = await Decilist([UniformProvider()], Policy(min_confidence=0)).decide(question)
    assert result.value == expected, "equal probabilities must follow JSON integer-key order across Python and JavaScript"
''',
    "translation_guard": '''
from decilist import Choose
from decilist.demo import APP_INSTRUCTIONS, APP_OPTIONS, walkthrough_rules
@pytest.mark.parametrize("text", ["Translate this summary into Arabic.", "Give a translation of the outline.", "Prepare a Spanish translation of this checklist."])
def test_explicit_unsupported_translation_is_reviewed(text):
    question = Choose(state=json.dumps({"app":"request-desk-v1", "request":text, "clarification":""}), instructions=APP_INSTRUCTIONS, options=APP_OPTIONS)
    probabilities = walkthrough_rules(question)
    assert probabilities["review"] == 1, "a supported format word must not authorize an unsupported translation action"
''',
    "flash_capability": '''
from decilist import LiquidLocalProvider
@pytest.mark.parametrize("device,compiled,expected_fused", [("cuda:1", False, False), ("cuda:1", True, True), ("cpu", False, True)])
def test_provider_respects_compiled_attention_capability(monkeypatch, device, compiled, expected_fused):
    # Capability/control-flow simulation only: no tensor, GPU, or native kernel is used.
    torch = ModuleType("torch")
    torch.float32, torch.bfloat16 = "float32", "bfloat16"
    torch.backends = SimpleNamespace(cuda=SimpleNamespace(is_flash_attention_available=lambda: compiled))
    class Model:
        def to(self, device): return self
        def eval(self): return self
    Model.__module__ = "fixture_vendor.modeling_d1"
    model = Model()
    hybrid = ModuleType("fixture_vendor.hybrid")
    hybrid._fused = lambda query: True
    hybrid._explicit = lambda *args, **kwargs: None
    transformers = ModuleType("transformers")
    transformers.AutoModel = SimpleNamespace(from_pretrained=lambda *args, **kwargs: model)
    hub = ModuleType("huggingface_hub")
    hub.snapshot_download = lambda *args, **kwargs: "verified-fixture-cache"
    for name, value in {"torch":torch, "transformers":transformers, "huggingface_hub":hub, "fixture_vendor.hybrid":hybrid}.items():
        monkeypatch.setitem(sys.modules, name, value)
    LiquidLocalProvider.load(allow_model_code=True, device=device, offline=True)
    assert hybrid._fused(object()) is expected_fused, f"Torch compiled Flash Attention availability={compiled}; the provider must select a compatible attention path for {device}"
''',
    "normalization": '''
from decilist import Choose, Decilist, FunctionProvider, Policy
@pytest.mark.parametrize("probabilities", [{"a":.2,"b":.2}, {"a":0,"b":0}, {"a":.7,"b":.6}])
async def test_invalid_probability_sum_cannot_be_accepted(probabilities):
    question = Choose(state="Observed input", instructions="Choose", options={"a":"A", "b":"B"})
    result = await Decilist([FunctionProvider(lambda _: probabilities)], Policy(min_confidence=0)).decide(question)
    assert result.status == "abstained", "provider probabilities must sum to one before an accepted output is formed"
    assert result.attempts[0].outcome == "invalid_output"
    assert result.value is None
''',
    "threshold_rejection": '''
from decilist import Choose, Decilist, FunctionProvider, Policy
@pytest.mark.parametrize("probability,threshold", [(.5,.7), (.6,.9), (.8,.95)])
async def test_probability_below_policy_threshold_abstains(probability, threshold):
    question = Choose(state="Observed input", instructions="Choose", options={"a":"A", "b":"B"})
    result = await Decilist([FunctionProvider(lambda _: {"a":probability,"b":1-probability})], Policy(min_confidence=threshold)).decide(question)
    assert result.status == "abstained", "a valid distribution below the configured policy threshold must abstain"
    assert result.value is None
    assert result.attempts[0].outcome == "low_confidence"
''',
    "threshold_boundary": '''
from decilist import Choose, Decilist, FunctionProvider, Policy
@pytest.mark.parametrize("threshold", [.7, .8, 1.0])
async def test_probability_at_inclusive_policy_threshold_is_accepted(threshold):
    question = Choose(state="Observed input", instructions="Choose", options={"a":"A", "b":"B"})
    result = await Decilist([FunctionProvider(lambda _: {"a":threshold,"b":1-threshold})], Policy(min_confidence=threshold)).decide(question)
    assert result.status == "accepted", "the policy threshold is inclusive; equality must not create an abstention"
    assert result.value == "a"
    assert result.attempts[0].outcome == "accepted"
''',
    "health_identity": '''
from decilist.runtime import RuntimeCompatibilityError, validate_runtime_health
@pytest.mark.parametrize("service", ["other-service", "", "decilist-copy"])
def test_endpoint_identity_is_checked_before_runtime_reuse(service):
    health = {"service":service,"schema_version":"1","runtime_version":"0.1.0a1","ready":True,"status":"ok","provider":"demo"}
    with pytest.raises(RuntimeCompatibilityError, match="not a Decilist runtime"):
        validate_runtime_health(health)
''',
    "simple_contract": '''
from decilist import Choose, Decilist, Policy, UniformProvider
@pytest.mark.parametrize("options", [{"a":"A","b":"B"}, {"left":"Left","right":"Right"}, {"one":"One","two":"Two","three":"Three"}])
async def test_valid_direct_decision_contract(options):
    result = await Decilist([UniformProvider()], Policy(min_confidence=0)).decide(Choose(state="Observed input", instructions="Choose", options=options))
    assert result.status == "accepted"
    assert set(result.probabilities) == set(options)
    assert abs(sum(result.probabilities.values())-1) < 1e-5
''',
    "format_parser": '''
from decilist.demo import app_formats
@pytest.mark.parametrize("text,expected", [("Prepare a summary", {"summary"}), ("Prepare an outline", {"outline"}), ("Prepare a checklist", {"checklist"})])
def test_supported_format_is_recognized(text, expected):
    assert app_formats(text) == expected, "known supported format must survive application parsing"
''',
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_catalog() -> dict:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    if catalog["base_revision"] != BASE_REVISION or len(catalog["cases"]) != 10:
        raise ValueError("unexpected frozen base or corpus size")
    ids = [case["id"] for case in catalog["cases"]]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case IDs")
    for case in catalog["cases"]:
        if case["test_key"] not in TESTS or not set(case["acceptable_routes"]) <= set(catalog["routes"]):
            raise ValueError("invalid test or route label")
        if any(not path.startswith(SOURCE_PREFIX) or ".." in Path(path).parts for path in case["editable_paths"]):
            raise ValueError("editable files must be scoped production source")
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
    return files


def _python() -> Path:
    path = Path(os.environ.get("DECILIST_TRIAGE_PYTHON", sys.executable)).resolve()
    if not path.is_file():
        raise ValueError("the selected Python executable is unavailable; no environment is installed automatically")
    return path


def _checked_path(path: str | Path, *, new: bool = False) -> Path:
    resolved, allowed = Path(path).resolve(), LAB_ROOT.resolve()
    if resolved == allowed or not resolved.is_relative_to(allowed) or resolved.is_relative_to(REPO_ROOT.resolve()):
        raise ValueError("case directories must be strictly inside workspace work/triage-lab and outside the checkout")
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
    mutation = case["mutation"]
    if mutation["kind"] == "none":
        return
    path = case_dir / mutation["path"]
    if mutation["kind"] == "historical_blob":
        path.write_bytes(_git_blob(mutation["revision"], mutation["path"]))
    elif mutation["kind"] == "replace":
        text = path.read_text(encoding="utf-8")
        if text.count(mutation["old"]) != 1:
            raise ValueError("scoped mutation must match exactly once")
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
    manifest = {"schema_version": 1, "case_id": case_id, "base_revision": BASE_REVISION,
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
            if relative.startswith(".triage/runs/") or "/__pycache__/" in relative:
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
    environment.update(PYTHONPATH=str(directory / "packages/python/src"), PYTHONDONTWRITEBYTECODE="1",
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
    expected_failure = case["role"] != "passing_control"
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
