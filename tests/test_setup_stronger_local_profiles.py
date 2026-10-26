"""Pure plans and stubbed setup checks; no downloads, GPUs or model processes."""
from contextlib import contextmanager, ExitStack
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("setup_stronger_local_draft", ROOT / "scripts/setup_stronger_local.py")
setup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = setup
SPEC.loader.exec_module(setup)


@contextmanager
def cache_directory():
    parent = ROOT / "work/setup-profile-tests"
    parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(dir=parent)
    path = Path(temporary.name).resolve()
    path.relative_to(ROOT.resolve())
    try:
        yield path
    finally:
        # Verify the exact recursive-cleanup target stays inside this draft.
        path.resolve().relative_to(ROOT.resolve())
        temporary.cleanup()


def arguments(cache, *extra):
    return setup.parse_args(["plan", "--cache-dir", str(cache), *extra])


def measurements(free=(24000, 16000), host=80_000_000_000):
    return {"gpus": [{"index": index, "uuid": "GPU-" + str(index), "free_mib": amount}
                     for index, amount in enumerate(free)], "host_ram": {"available_bytes": host}}


class ProfilesTests(unittest.TestCase):
    def test_coder_default_argv_and_identity_are_preserved(self):
        with cache_directory() as cache:
            args = arguments(cache)
            plan = setup.plan(args)
            expected = [str(cache / "runtime/llama-server.exe"), "--model", str(cache / "model" / setup.MODEL_FILE),
                "--alias", setup.ALIAS, "--host", "127.0.0.1", "--port", "8792", "--ctx-size", "8192", "--parallel", "1",
                "--n-gpu-layers", "99", "--split-mode", "none", "--main-gpu", "0", "--fit", "off", "--batch-size", "512",
                "--ubatch-size", "128", "--flash-attn", "on", "--jinja", "--offline", "--no-context-shift", "--no-webui"]
            self.assertEqual(plan["argv"], expected)
            self.assertEqual(plan["profile"]["revision"], "0ca51122012fe4a20dc82988ee58780b008ce4e3")
            self.assertEqual(plan["selected_environment"]["CUDA_VISIBLE_DEVICES"], "0")
            self.assertIsNone(plan["settings"]["minimum_free_gpu_mib"])

    def test_new_profile_matches_the_audited_pin_and_launch_flags(self):
        # Independent expected values transcribed from the retained V6 download
        # pin and launch plan. No external files, model or network are required.
        pin = ("ggml-org/Qwen3.6-27B-GGUF", "1132d03976d88e30a09ab0ddbf1531d5e1b46e7c",
               "Qwen3.6-27B-Q8_0.gguf", 28595762464,
               "73f8260284708ed78ae266df672288b6ad1f2c73ec7ffeb7514b5cecdba646c9")
        with cache_directory() as cache:
            plan = setup.plan(arguments(cache, "--profile", "qwen36-27b-q8"))
        profile, chosen, argv = plan["profile"], plan["settings"], plan["argv"]
        self.assertEqual((profile["repo"], profile["revision"], profile["file"], profile["bytes"], profile["sha256"]),
                         pin)
        self.assertEqual(profile["license"], "Apache-2.0")
        self.assertEqual(chosen["context"], 16384)
        self.assertEqual(chosen["port"], 8794)
        self.assertEqual(chosen["minimum_free_gpu_mib"], (20480, 14336))
        self.assertEqual(plan["selected_environment"]["CUDA_VISIBLE_DEVICES"], "0,1")
        self.assertEqual(argv[argv.index("--tensor-split") + 1], "3,2")
        self.assertEqual(argv[argv.index("--reasoning") + 1], "off")
        self.assertEqual(argv[argv.index("--reasoning-format") + 1], "none")
        self.assertEqual(argv[argv.index("--cache-type-k") + 1], "f16")
        self.assertEqual(argv[argv.index("--batch-size") + 1], "256")
        self.assertNotIn("--chat-template-kwargs", argv)
        self.assertIn("pending", plan["compatibility"])

    def test_legacy_explicit_overrides_still_work(self):
        with cache_directory() as cache:
            plan = setup.plan(arguments(cache, "--gpu", "1", "--port", "8850", "--context", "16384"))
        self.assertEqual(plan["selected_environment"]["CUDA_VISIBLE_DEVICES"], "1")
        self.assertEqual(plan["settings"]["port"], 8850)
        self.assertEqual(plan["settings"]["context"], 16384)

    def test_custom_resource_layout_requires_and_records_an_explicit_budget(self):
        with cache_directory() as cache:
            with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                arguments(cache, "--profile", "qwen36-27b-q8", "--gpus", "0", "--gpu-layers", "20")
            plan = setup.plan(arguments(cache, "--profile", "qwen36-27b-q8", "--gpus", "2", "--gpu-layers", "20",
                                        "--min-free-gpu-mib", "12000", "--host-reserve-gb", "12"))
        self.assertEqual(plan["settings"]["minimum_free_gpu_mib"], (12000,))
        self.assertEqual(plan["settings"]["resource_budget_source"], "explicit user budget")
        self.assertEqual(plan["settings"]["host_reserve_bytes"], 12_000_000_000)
        self.assertEqual(plan["argv"][plan["argv"].index("--split-mode") + 1], "none")

    def test_invalid_settings_are_rejected_without_a_model_or_network(self):
        bad = [("--gpus", "0,0"), ("--gpus", "0,-1"), ("--gpus", "0, 1"), ("--port", "0"),
               ("--context", "32769"), ("--gpu-layers", "1000"), ("--tensor-split", "nan,1"),
               ("--tensor-split", "0,1"), ("--gpus", "0,1,2"), ("--main-gpu", "2"),
               ("--main-gpu", "1"),
               ("--batch-size", "64", "--ubatch-size", "65"), ("--min-free-gpu-mib", "0,14000"),
               ("--min-free-gpu-mib", "20000"), ("--host-reserve-gb", "inf")]
        with cache_directory() as cache, patch.object(setup.subprocess, "Popen") as popen, patch.object(setup.urllib.request, "urlopen") as network:
            for flags in bad:
                with self.subTest(flags=flags), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                    arguments(cache, "--profile", "qwen36-27b-q8", *flags)
            popen.assert_not_called()
            network.assert_not_called()

    def test_plan_is_side_effect_free_with_all_storage_paths_under_the_requested_root(self):
        with cache_directory() as cache:
            target = cache / "does-not-exist"
            with patch.object(setup.subprocess, "Popen") as popen, patch.object(setup.urllib.request, "urlopen") as network, patch("sys.stdout", io.StringIO()):
                self.assertEqual(setup.main(["plan", "--profile", "qwen36-27b-q8", "--cache-dir", str(target)]), 0)
                popen.assert_not_called()
                network.assert_not_called()
            self.assertFalse(target.exists())
            for value in setup.storage_paths(target.resolve()).values():
                Path(value).relative_to(target.resolve())

    def test_real_cli_plan_does_not_create_its_cache(self):
        with cache_directory() as cache:
            target = cache / "future-model-cache"
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/setup_stronger_local.py"), "plan", "--profile", "qwen36-27b-q8", "--cache-dir", str(target)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual((plan["runtime_actions"], plan["network_actions"]), (0, 0))
            self.assertFalse(target.exists())

    def test_storage_overrides_and_restores_all_inherited_cache_and_temp_locations(self):
        with cache_directory() as cache, patch.dict(os.environ, {"HF_XET_CACHE": "C:/unrelated-cache", "TEMP": "C:/unrelated-temp", "HF_TOKEN": "unit-test-placeholder"}):
            prior_temp = tempfile.tempdir
            with setup.download_storage(cache):
                self.assertEqual(Path(tempfile.gettempdir()), cache / "tmp")
                self.assertNotIn("HF_TOKEN", os.environ)
                for key, value in setup.storage_paths(cache).items():
                    self.assertEqual(os.environ[key], value)
                    Path(value).resolve().relative_to(cache)
            self.assertEqual(os.environ["HF_XET_CACHE"], "C:/unrelated-cache")
            self.assertEqual(tempfile.tempdir, prior_temp)

    def test_child_environment_isolated_but_user_supplied_dlls_are_reused(self):
        with cache_directory() as cache, patch.dict(os.environ, {"llama_arg_rpc": "remote-unit-placeholder", "HF_TOKEN": "unit-token"}):
            args = arguments(cache, "--profile", "qwen36-27b-q8", "--cuda-dll-dir", str(cache / "existing-dlls"))
            env, selected = setup.child_environment(args, setup.settings(args), cache)
            self.assertNotIn("llama_arg_rpc", env)
            self.assertNotIn("HF_TOKEN", env)
            self.assertTrue(env["PATH"].startswith(str(cache / "existing-dlls")))
            for key in setup.storage_paths(cache):
                Path(selected[key]).relative_to(cache)
            self.assertEqual(selected["HF_HUB_OFFLINE"], "1")

    def test_download_uses_selected_pin_only_and_never_launches(self):
        with cache_directory() as cache:
            args = arguments(cache, "--profile", "qwen36-27b-q8", "--cuda-dll-dir", str(cache / "dlls"))
            fake_model = cache / "model/Qwen3.6-27B-Q8_0.gguf"
            with patch.object(setup, "require_windows"), patch.object(setup, "hub_download", return_value=fake_model) as hub, patch.object(setup, "verify", return_value={"file": fake_model.name}) as verify, patch.object(setup, "download_archive", return_value={"stub": True}) as archives, patch.object(setup.shutil, "disk_usage", return_value=SimpleNamespace(free=100_000_000_000)), patch.object(setup.subprocess, "Popen") as popen, patch("sys.stdout", io.StringIO()):
                setup.explicit_download(args)
            selected = setup.PROFILES["qwen36-27b-q8"]
            hub.assert_called_once_with(selected, cache)
            verify.assert_called_once_with(fake_model, 28595762464, selected.sha256)
            self.assertEqual(archives.call_count, 1)
            popen.assert_not_called()
            record = json.loads((cache / "explicit-setup-manifest-qwen36-27b-q8.json").read_text())
            self.assertEqual(record["runtime_actions"], 0)
            self.assertEqual(record["model"]["license"], "Apache-2.0")

    def test_cached_xet_import_is_rejected_without_network(self):
        with cache_directory() as cache, patch.dict(sys.modules, {"hf_xet": SimpleNamespace()}), patch.object(setup.urllib.request, "urlopen") as network:
            with self.assertRaisesRegex(RuntimeError, "fresh CLI"):
                setup.hub_download(setup.PROFILES["qwen36-27b-q8"], cache)
            network.assert_not_called()

    def test_low_gpu_memory_fails_before_hashes_and_process_creation(self):
        socket = Mock()
        socket.return_value.__enter__ = Mock(return_value=Mock())
        socket.return_value.__exit__ = Mock(return_value=False)
        with cache_directory() as cache, patch.object(setup, "require_windows"), patch.object(setup.socket, "socket", socket), patch.object(setup, "collect_resources", return_value=measurements((1000, 1000))), patch.object(setup, "verify") as verify, patch.object(setup.subprocess, "Popen") as popen:
            args = arguments(cache, "--profile", "qwen36-27b-q8")
            with self.assertRaisesRegex(RuntimeError, "headroom"):
                setup.serve(args)
            verify.assert_not_called()
            popen.assert_not_called()
            records = list(cache.glob("resource-check-*.json"))
            self.assertEqual(len(records), 1)
            self.assertFalse(json.loads(records[0].read_text())["passed"])

    def test_occupied_port_is_never_reused_or_stopped(self):
        socket = Mock()
        socket.return_value.__enter__ = Mock(return_value=Mock(bind=Mock(side_effect=OSError("occupied"))))
        socket.return_value.__exit__ = Mock(return_value=False)
        with cache_directory() as cache, patch.object(setup, "require_windows"), patch.object(setup.socket, "socket", socket), patch.object(setup, "collect_resources") as resources, patch.object(setup, "verify") as verify, patch.object(setup.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(OSError, "occupied"):
                setup.serve(arguments(cache, "--profile", "qwen36-27b-q8"))
            resources.assert_not_called()
            verify.assert_not_called()
            popen.assert_not_called()

    def test_resource_host_disk_and_missing_gpu_gates(self):
        with cache_directory() as cache:
            args = arguments(cache, "--profile", "qwen36-27b-q8")
            profile, chosen = setup.selected_profile(args), setup.settings(args)
            for hardware, free, message in [(measurements((24000,), host=100_000_000_000), 10_000_000_000, "headroom"), (measurements(host=10), 10_000_000_000, "host RAM"), (measurements(), 1, "3 GB")]:
                with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, message):
                    setup.require_resources(profile, chosen, hardware, free)

    def test_native_default_prefix_proof_is_metadata_only_and_has_no_thinking_override(self):
        response = io.BytesIO(json.dumps({"prompt": "prefix" + setup.CLOSED_THINKING_PREFIX}).encode())
        opener = Mock()
        opener.open.return_value = response
        result = setup.nonthinking_proof(opener, "http://127.0.0.1:8794")
        request = opener.open.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/apply-template"))
        self.assertNotIn("chat_template_kwargs", json.loads(request.data))
        self.assertEqual(result["generation_requests"], 0)
        self.assertTrue(result["passed"])

    def test_an_open_thinking_prefix_and_redirects_fail_closed(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps({"prompt": "<|im_start|>assistant\n<think>\n"}).encode())
        with self.assertRaisesRegex(RuntimeError, "closed non-thinking"):
            setup.nonthinking_proof(opener, "http://127.0.0.1:8794")
        with self.assertRaisesRegex(RuntimeError, "redirect"):
            setup.NoRedirect().redirect_request(None, None, None, None, None, None)

    def test_stubbed_new_launch_keeps_old_config_and_requires_actual_default_mode_proof(self):
        class Child:
            pid = 321
            finished = False
            terminated = False
            def poll(self): return 0 if self.finished else None
            def wait(self, timeout=None):
                self.finished = True
                return 0
            def terminate(self):
                self.terminated = True
                self.finished = True

        for prefix_ok in (True, False):
            with self.subTest(prefix_ok=prefix_ok), cache_directory() as cache, ExitStack() as stack:
                args = arguments(cache, "--profile", "qwen36-27b-q8")
                model = cache / "model" / setup.PROFILES["qwen36-27b-q8"].file
                model.parent.mkdir()
                model.write_bytes(b"stub, not model weights")
                runtime = cache / "runtime"
                runtime.mkdir()
                for name in ("llama-server.exe", *setup.DLL_NAMES):
                    (runtime / name).write_bytes(b"stub, never executed")
                old = cache / "decilist-model.json"
                old.write_text("old identity must remain unchanged")
                socket = Mock()
                socket.return_value.__enter__ = Mock(return_value=Mock())
                socket.return_value.__exit__ = Mock(return_value=False)
                child = Child()
                popen = stack.enter_context(patch.object(setup.subprocess, "Popen", return_value=child))
                stack.enter_context(patch.object(setup, "require_windows"))
                stack.enter_context(patch.object(setup.socket, "socket", socket))
                stack.enter_context(patch.object(setup, "collect_resources", return_value=measurements()))
                stack.enter_context(patch.object(setup, "verify", return_value={"file": model.name}))
                stack.enter_context(patch.object(setup, "digest", return_value=setup.SERVER_EXE_SHA256))
                def run(command, **_kwargs):
                    if command[-1] == "--version":
                        return SimpleNamespace(stdout="build 11889 commit d854d98ba", stderr="")
                    if command[-1] == "--list-devices":
                        return SimpleNamespace(stdout="CUDA0: stub\nCUDA1: stub", stderr="")
                    if ".Modules" in command[-1]:
                        return SimpleNamespace(stdout=json.dumps([{"ModuleName": name, "FileName": str(runtime / name)} for name in setup.DLL_NAMES]), stderr="")
                    return SimpleNamespace(stdout="[321]", stderr="")
                stack.enter_context(patch.object(setup.subprocess, "run", side_effect=run))
                opener = Mock()
                health = io.BytesIO(b'{"status":"ok"}')
                health.status = 200
                opener.open.return_value = health
                stack.enter_context(patch.object(setup.urllib.request, "build_opener", return_value=opener))
                def metadata(_opener, _url, endpoint, body=None):
                    if endpoint == "/props":
                        return {"model_alias": "qwen36-27b-q8", "build_info": "b11889-d854d98ba", "model_path": str(model), "default_generation_settings": {"n_ctx": 16384}}
                    if endpoint == "/v1/models":
                        return {"data": [{"id": "qwen36-27b-q8", "meta": {"n_ctx": 16384}}]}
                    self.assertEqual(endpoint, "/apply-template")
                    self.assertNotIn("chat_template_kwargs", body)
                    return {"prompt": setup.CLOSED_THINKING_PREFIX if prefix_ok else "<|im_start|>assistant\n<think>\n"}
                stack.enter_context(patch.object(setup, "metadata", side_effect=metadata))
                stack.enter_context(patch("sys.stdout", io.StringIO()))
                if prefix_ok:
                    self.assertEqual(setup.serve(args), 0)
                    configured = json.loads((cache / "decilist-model-qwen36-27b-q8.json").read_text())
                    self.assertEqual(configured, {"model": "qwen36-27b-q8", "expected_build": "b11889-d854d98ba"})
                    self.assertFalse(child.terminated)
                    record = json.loads(next(cache.glob("launches/*/server-ready-manifest.json")).read_text())
                    self.assertEqual(record["generation_requests_by_setup"], 0)
                    self.assertTrue(record["nonthinking_template_proof"]["passed"])
                    self.assertEqual(len(record["actually_loaded_cuda_modules"]), 3)
                    self.assertEqual(popen.call_args.kwargs["env"]["CUDA_VISIBLE_DEVICES"], "GPU-0,GPU-1")
                else:
                    with self.assertRaisesRegex(RuntimeError, "closed non-thinking"):
                        setup.serve(args)
                    self.assertTrue(child.terminated)
                    self.assertFalse((cache / "decilist-model-qwen36-27b-q8.json").exists())
                    self.assertEqual(len(list(cache.glob("launches/*/failure.json"))), 1)
                self.assertEqual(old.read_text(), "old identity must remain unchanged")

    def test_loaded_cuda_dependency_drift_is_rejected(self):
        with cache_directory() as cache:
            configured = [{"file": name, "sha256": "expected"} for name in setup.DLL_NAMES]
            wrong = [{"ModuleName": name, "FileName": str(cache / "other-directory" / name)} for name in setup.DLL_NAMES]
            with patch.object(setup.subprocess, "run", return_value=SimpleNamespace(stdout=json.dumps(wrong))), patch.object(setup, "digest", return_value="expected"):
                with self.assertRaisesRegex(RuntimeError, "outside the configured"):
                    setup.loaded_cuda_modules(321, cache / "expected-directory", configured)

    def test_resource_loss_during_verification_is_rechecked_before_launch(self):
        with cache_directory() as cache, ExitStack() as stack:
            args = arguments(cache, "--profile", "qwen36-27b-q8")
            runtime = cache / "runtime"
            runtime.mkdir()
            for name in ("llama-server.exe", *setup.DLL_NAMES):
                (runtime / name).write_bytes(b"stub, never executed")
            socket = Mock()
            socket.return_value.__enter__ = Mock(return_value=Mock())
            socket.return_value.__exit__ = Mock(return_value=False)
            stack.enter_context(patch.object(setup, "require_windows"))
            stack.enter_context(patch.object(setup.socket, "socket", socket))
            stack.enter_context(patch.object(setup, "collect_resources", side_effect=[measurements(), measurements((1000, 1000))]))
            stack.enter_context(patch.object(setup, "verify", return_value={"file": "stub"}))
            stack.enter_context(patch.object(setup, "digest", return_value=setup.SERVER_EXE_SHA256))
            stack.enter_context(patch.object(setup.subprocess, "run", side_effect=[
                SimpleNamespace(stdout="build 11889 commit d854d98ba", stderr=""),
                SimpleNamespace(stdout="CUDA0: stub\nCUDA1: stub", stderr="")]))
            popen = stack.enter_context(patch.object(setup.subprocess, "Popen"))
            with self.assertRaisesRegex(RuntimeError, "headroom"):
                setup.serve(args)
            popen.assert_not_called()
            failures = list(cache.glob("launches/*/failure.json"))
            self.assertEqual(len(failures), 1)
            failure = json.loads(failures[0].read_text())
            self.assertEqual(failure["launch"]["hardware_immediately_before_spawn"]["gpus"][0]["free_mib"], 1000)

    def test_an_existing_cache_link_cannot_redirect_storage_outside_the_root(self):
        with cache_directory() as parent:
            cache, outside = parent / "cache", parent / "outside"
            cache.mkdir()
            outside.mkdir()
            try:
                (cache / "hf-home").symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"Local OS does not allow symlink creation: {type(error).__name__}")
            with self.assertRaises(ValueError):
                setup.validate_owned_paths(cache)

    def test_existing_artifact_or_local_metadata_links_fail_before_any_hub_call(self):
        for profile in setup.PROFILES.values():
            for relative, directory in ((profile.file, False), (".cache", True),
                    (".cache/huggingface", True), (".cache/huggingface/download", True),
                    (".cache/huggingface/.gitignore", False),
                    (f".cache/huggingface/download/{profile.file}.metadata", False)):
                with self.subTest(profile=profile.key, path=relative), cache_directory() as parent:
                    cache, outside = parent / "cache", parent / "outside"
                    cache.mkdir()
                    outside.mkdir()
                    target = outside if directory else outside / "unchanged.txt"
                    if not directory:
                        target.write_bytes(b"do not write outside the selected cache")
                    link = cache / "model" / relative
                    link.parent.mkdir(parents=True)
                    try:
                        link.symlink_to(target, target_is_directory=directory)
                    except OSError as error:
                        self.skipTest(f"Local OS does not allow symlink creation: {type(error).__name__}")
                    with patch.object(setup, "require_windows"), patch.object(setup, "hub_download") as hub, patch.object(setup, "download_archive") as archive:
                        with self.assertRaises(ValueError):
                            setup.explicit_download(arguments(cache, "--profile", profile.key))
                        hub.assert_not_called()
                        archive.assert_not_called()
                    if not directory:
                        self.assertEqual(target.read_bytes(), b"do not write outside the selected cache")


if __name__ == "__main__":
    unittest.main()
