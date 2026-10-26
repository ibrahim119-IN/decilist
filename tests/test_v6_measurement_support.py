"""Synthetic V6 measurement fixtures; no corpus rows or live runtime access."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from benchmarks import run_triage_v6 as runner
from test_triage_v5_trial import Lab as BaseLab, repair, change_audit_collection


SUFFIXES = {"qwen3_coder_non_reasoning": "<|im_start|>assistant\n",
            "qwen3_6_closed_thinking": "<|im_start|>assistant\n<think>\n\n</think>\n\n"}


def mode_proof(config):
    suffix = SUFFIXES[config["generation_profile"]]
    return {"schema_version": 1, "status": "verified", "generation_profile": config["generation_profile"],
        "mode": "native_non_reasoning" if "coder" in config["generation_profile"] else "explicit_closed_thinking",
        "expected_chat_template_sha256": config["expected_chat_template_sha256"],
        "actual_chat_template_sha256": config["expected_chat_template_sha256"],
        "expected_prompt_suffix": suffix, "actual_prompt_suffix": suffix,
        "prompt_suffix_sha256": hashlib.sha256(suffix.encode()).hexdigest(), "prompt_sha256": "f" * 64,
        "suffix_matches": True, "per_request_template_override": False, "reasoning_capability_evaluated": False,
        "build_info": config["expected_build"], "n_ctx": 16384, "model": config["model"]}


def validate_mode(proof, *, generation_profile, expected_chat_template_sha256, model=None, expected_build=runner.EXPECTED_BUILD):
    expected = mode_proof({"generation_profile": generation_profile,
        "expected_chat_template_sha256": expected_chat_template_sha256, "model": model,
        "expected_build": expected_build})
    if not isinstance(proof, dict) or any(proof.get(key) != value or type(proof.get(key)) is not type(value)
        for key, value in expected.items()):
        raise ValueError("invalid actual native generation mode proof")
    return deepcopy(proof)


def ready(config):
    return {"model": config["model"], "build_info": config["expected_build"], "n_ctx": 16384,
        "chat_template_sha256": config["expected_chat_template_sha256"], "local_only": True}


class Provider:
    calls = []

    def __init__(self, config=None, endpoint=None, *, base_url=None, **identity):
        config = config if config is not None else identity
        endpoint = endpoint if endpoint is not None else base_url
        self.config, self.endpoint, self.generation_mode_proof = config, endpoint, None
        self.closed = False
        for key, value in config.items():
            setattr(self, key, value)

    async def ready(self):
        self.calls.append((self.model, "ready"))
        return ready(self.config)

    async def apply_template(self, messages):
        self.calls.append((self.model, "native_template"))
        self.generation_mode_proof = mode_proof(self.config)
        return "Synthetic rendered messages" + SUFFIXES[self.generation_profile]

    async def complete(self, *args):
        raise AssertionError("measurement preflight must never generate tokens")

    async def aclose(self):
        self.closed = True


class Lab(BaseLab):
    def __init__(self, root):
        super().__init__(root)
        self.freeze_calls, self.membership_calls = [], []

    def verify_freeze(self, *, require_retained_proof=False):
        self.freeze_calls.append(require_retained_proof)
        if require_retained_proof is not True:
            raise AssertionError("evaluation must demand actual retained proof")
        return {"retained_proof_verified": True}

    def case_ids(self, split):
        self.membership_calls.append(split)
        return runner.CASE_ORDER[split]

    def prepare_machine_evidence(self, directory, observed):
        return self.prepare_case_evidence(directory, observed)


def make_fixture(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    (root / "source.py").write_text("VALUE = 1\n")
    fake_lab = Lab(tmp_path / "lab")
    fake_lab.FREEZE, fake_lab.CATALOG = root / "freeze.json", root / "catalog.json"
    fake_lab.FREEZE.write_text('{"unit":"frozen identity"}')
    fake_lab.CATALOG.write_text('{"unit":"no held-out content"}')
    monkeypatch.setattr(runner, "ROOT", root)
    monkeypatch.setattr(runner, "lab", fake_lab)
    monkeypatch.setattr(runner, "_source_files", lambda: ("source.py",))
    def loader(path):
        value = json.loads(Path(path).read_text())
        required = {"model", "expected_build", "generation_profile", "expected_chat_template_sha256"}
        if not required <= set(value) or set(value) - required - {"expected_model_path"}:
            raise ValueError("explicit generation profile/template hash required")
        return value
    workflow = SimpleNamespace(VERSION="v6-unit-workflow", load_model_config=loader)
    generator = SimpleNamespace(specification=lambda: {"unit": "same instructions and generation policy"},
        validate_generation_mode_proof=validate_mode, V6LlamaCppProvider=Provider,
        MODE_PROBE_MESSAGES=[{"role": "system", "content": "Template identity probe only."}, {"role": "user", "content": "Return JSON."}])
    monkeypatch.setattr(runner, "_workflow_module", lambda: workflow)
    monkeypatch.setattr(runner, "_generator_module", lambda: generator)
    configs, profiles = {}, {}
    for index, role in enumerate(runner.PROFILES):
        config = {"model": "synthetic-" + role, "expected_build": runner.EXPECTED_BUILD,
            "generation_profile": runner.GENERATION_PROFILES[role], "expected_chat_template_sha256": ("a" if index == 0 else "b") * 64}
        path = root / (role + ".json")
        path.write_text(json.dumps(config))
        configs[role] = path
        profiles[role] = {"config_path": str(path), "endpoint": "http://127.0.0.1:8792",
            "protocol_metadata": {"ready_identity": ready(config), "generation_mode_proof": mode_proof(config),
                "model_artifact_sha256": runner.EXPECTED_WEIGHTS[role], "runtime_binary_sha256": "c" * 64,
                "launch_settings": {"ctx": 16384, "profile": config["generation_profile"]}}}
    manifest = root / "profiles.json"
    manifest.write_text(json.dumps({"schema_version": 1, "profiles": profiles}))
    Provider.calls = []
    return SimpleNamespace(root=root, lab=fake_lab, profiles=manifest, configs=configs, workflow=workflow,
        generator=generator, matrix_root=fake_lab.LAB_ROOT / "matrices" / "synthetic")


def candidate_file(fixture):
    path = fixture.root / "candidate.json"
    if not path.exists():
        path.write_bytes(runner._json_bytes(runner.candidate_snapshot(fixture.profiles)))
    return path


def proof_file(fixture, ids=None):
    ids = ids or runner.HELDOUT_ORDER
    rows = []
    for case in ids:
        project = fixture.lab.LAB_ROOT / "synthetic-proof" / case
        if project.exists():
            continue
        fixture.lab.prepare_case(case, project)
        seeded = fixture.lab.run_case(project)
        baseline = {**seeded, "exit_code": 0}
        rows.append({"case_id": case, "verified": True, "baseline": baseline, "mutation": seeded,
            "reference_repair": {"passed": True, "run": baseline}, "seeded_source_sha256": seeded["editable_sha256_after"]})
    target = fixture.root / ("proof-" + str(len(ids)) + ".json")
    if rows:
        target.write_text(json.dumps({"cases": rows}))
    return target


def args(fixture, *, index="one", **changes):
    value = dict(split="matrix", batch="current-v5", model_profile="current", context_policy="v6",
        model_config=fixture.configs["current"], qwen_url="http://127.0.0.1:8792", profiles=fixture.profiles,
        candidate_freeze=candidate_file(fixture), matrix_root=fixture.matrix_root,
        proof=fixture.root / "missing-proof.json", output=fixture.root / (index + ".public.json"),
        private_output=fixture.root / (index + ".private.json"), apply=True, replay=False, work_dir=None)
    value.update(changes)
    return SimpleNamespace(**value)


async def abstain(evidence, **kwargs):
    return {"schema_version": 6, "state": "unverified", "context_policy": kwargs["context_policy"],
        "attempts": [], "reason": "synthetic retained abstention", "apply": {"requested": kwargs["apply"], "applied": False}}


async def applied(evidence, **kwargs):
    repair({"case_dir": str(kwargs["project"])})
    return {"schema_version": 6, "state": "hypothesis_supported_by_tests", "context_policy": kwargs["context_policy"],
        "attempts": [], "apply": {"requested": kwargs["apply"], "applied": True}}
