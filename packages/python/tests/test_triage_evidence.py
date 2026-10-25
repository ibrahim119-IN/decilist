import json
import os
from pathlib import Path
import socket
import subprocess

import pytest

from decilist.triage_evidence import has_failure_signal, prepare_evidence


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    return root


def source(project, name="src/example.py", text="first = 1\nassert first == 2\nlast = 3\n"):
    path = project / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_real_traceback_pytest_and_javascript_references(project):
    python = source(project)
    source(project, "tests/test_example.py")
    js = source(project, "src/main.ts", "const first = 1;\nthrow new Error('failed');\n")
    log = (f'Traceback (most recent call last):\n  File "{python}", line 2, in test\n'
           "AssertionError: expected two\ntests/test_example.py:2: AssertionError\n"
           f"    at test ({js}:2:7)\n1 failed, 2 passed")
    result = prepare_evidence(log, project, ["python", "-m", "pytest"])
    assert [(row["path"], row["line"]) for row in result["references"]] == [
        ("src/example.py", 2), ("tests/test_example.py", 2), ("src/main.ts", 2)]
    assert "2 | assert first == 2" in result["references"][0]["excerpt"]
    assert str(project) not in json.dumps(result)
    assert result["schema_version"] == 1
    assert result["command"] == ["python", "-m", "pytest"]
    for signal in result["signals"]:
        assert signal["text"] == result["log"].splitlines()[signal["line"] - 1]
    assert has_failure_signal(result["log"])


def test_deduplicates_locations_and_supports_firefox_and_file_uri(project):
    path = source(project, "src/main.mjs")
    result = prepare_evidence(f"at fn ({path.as_uri()}:2:4)\nfn@{path}:2:4", project)
    assert len(result["references"]) == 1
    assert result["references"][0]["path"] == "src/main.mjs"
    assert not has_failure_signal(result["log"])


def test_project_uri_placeholder_and_malformed_uris_do_not_crash(project):
    source(project, "src/main.js")
    result = prepare_evidence("at fn (file://[PROJECT]/src/main.js:2:4)\n"
                              "at fn (file://[broken[PROJECT]/src/main.js:2:4)\n"
                              "at fn (file://[invalid]/src/main.js:2:4)", project)
    assert [(row["path"], row["line"]) for row in result["references"]] == [("src/main.js", 2)]


def test_posix_style_file_uri_after_actual_prefix_replacement(project):
    path = source(project, "src/main.cjs")
    # Includes the netloc-looking placeholder produced for a POSIX absolute root,
    # and the triple-slash form produced for a Windows drive root.
    for uri in (path.as_uri(), "file://" + path.as_posix()):
        result = prepare_evidence(f"at fn ({uri}:2:9)", project)
        assert [row["path"] for row in result["references"]] == ["src/main.cjs"]


def test_uri_percent_encoded_spaces_are_resolved_locally(project):
    path = source(project, "src/main script.js")
    result = prepare_evidence(f"at fn ({path.as_uri()}:2:3)", project)
    assert [row["path"] for row in result["references"]] == ["src/main script.js"]


@pytest.mark.parametrize("uri", [
    "file://[PROJECT]/src/main%20script.js",
    "file:///[PROJECT]/src/main%20script.js",
])
def test_sanitized_project_uri_decodes_spaces_on_every_host(project, uri):
    source(project, "src/main script.js")
    result = prepare_evidence(f"at fn ({uri}:2:3)", project)
    assert [(row["path"], row["line"]) for row in result["references"]] == [("src/main script.js", 2)]
    assert "2 | assert first == 2" in result["references"][0]["excerpt"]


@pytest.mark.parametrize("uri", [
    "file://[PROJECT]/%2e%2e/outside.js",
    "file:///[PROJECT]/%2e%2e/outside.js",
])
def test_sanitized_project_uri_validates_traversal_after_decoding(project, uri):
    outside = project.parent / "outside.js"
    outside.write_text("OUTSIDE_CONTENT", encoding="utf-8")
    result = prepare_evidence(f"at fn ({uri}:1:3)", project)
    assert result["references"] == []
    assert "OUTSIDE_CONTENT" not in json.dumps(result)


def test_remote_uris_are_never_treated_as_project_relative_files(project):
    result = prepare_evidence("at fn (https://example.com/src/main.js:2:3)\n"
                              "at fn (file://server/share/src/main.js:2:3)", project)
    assert result["references"] == []


def test_project_marker_inside_external_path_does_not_bypass_normalization(project):
    source(project, "main.py")
    result = prepare_evidence('  File "/home/private-user/[PROJECT]/main.py", line 1\n'
                              '  File "[PROJECT]main.py", line 1', project)
    assert result["references"] == []
    assert "private-user" not in result["log"]


def test_project_paths_in_either_separator_style_read_same_real_fixture(project):
    path = source(project, "src/main.py")
    for spelling in (str(path).replace("\\", "/"), str(path).replace("/", "\\")):
        result = prepare_evidence(f'  File "{spelling}", line 2\nValueError: failed', project)
        assert [row["path"] for row in result["references"]] == ["src/main.py"]
        assert "[PROJECT]" in result["log"]
        assert str(project) not in result["log"]


def test_outside_absolute_paths_are_normalized_and_never_read(project, tmp_path, monkeypatch):
    outside = tmp_path / "outside.py"
    outside.write_text("DO_NOT_READ_OUTSIDE", encoding="utf-8")
    original = Path.open
    opened = []

    def guarded(path, *args, **kwargs):
        opened.append(path)
        assert path.resolve().is_relative_to(project.resolve())
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    log = (f'  File "{outside}", line 1\n'
           '  File "C:\\Users\\private-user\\venv\\outside.py", line 1\n'
           '  File "/home/private-user/venv/outside.py", line 1\n'
           "TypeError: failed")
    result = prepare_evidence(log, project)
    assert result["references"] == [] and opened == []
    assert "private-user" not in result["log"]
    assert result["log"].count("[EXTERNAL]/outside.py") == 3


@pytest.mark.parametrize("name", ["../outside.py", "src/../../outside.py", "secrets.py", "credentials.ts", ".env.js", "private_key.py", "config/token.ts"])
def test_traversal_and_secret_named_files_never_become_references(project, name):
    if ".." not in name:
        source(project, name, "SHOULD_NOT_BE_INCLUDED")
    result = prepare_evidence(f'  File "{name}", line 1\nValueError: failed', project)
    assert result["references"] == []
    assert "SHOULD_NOT_BE_INCLUDED" not in json.dumps(result)


def test_symlink_escape_is_rejected(project, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("OUTSIDE_SECRET", encoding="utf-8")
    link = project / "linked.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this host")
    result = prepare_evidence('  File "linked.py", line 1\nValueError: failed', project)
    assert result["references"] == []
    assert "OUTSIDE_SECRET" not in json.dumps(result)


def test_redacts_common_credentials_in_log_command_and_source(project):
    path = source(project, text="api_key = 'source-private-123'\nraise ValueError('failed')\n")
    log = (f'  File "{path}", line 2\nAuthorization: Bearer auth-private-123\n'
           'password="a secret with spaces"\n{"api_token": "json-private-123"}\n'
           "sk-abcdefghijklmnop123456\nValueError: failed")
    result = prepare_evidence(log, project, ["pytest", "--token", "command-private-123", "--password=other-private-123"])
    rendered = json.dumps(result)
    for secret in ("source-private-123", "auth-private-123", "a secret with spaces", "json-private-123", "sk-abcdefghijklmnop123456", "command-private-123", "other-private-123"):
        assert secret not in rendered
    assert result["redaction_count"] >= 7
    assert result["command"][2] == "[REDACTED]"


def test_redacts_cloud_secret_access_keys():
    result = prepare_evidence("AWS_SECRET_ACCESS_KEY=cloud-private-123\nOPENAI_API_KEY=openai-private-123")
    assert "cloud-private-123" not in result["log"]
    assert "openai-private-123" not in result["log"]


def test_private_keys_are_removed_even_when_end_is_truncated(project):
    log = "-----BEGIN RSA PRIVATE KEY-----\nULTRAPRIVATEKEYDATA\n-----END RSA PRIVATE KEY-----\nValueError: failed"
    result = prepare_evidence(log, project)
    assert "ULTRAPRIVATEKEYDATA" not in result["log"]
    assert result["redaction_count"] >= 1
    assert result["signals"][-1]["line"] == 4
    clipped = prepare_evidence("-----BEGIN PRIVATE KEY-----\n" + "sensitive" * 10_000)
    assert "sensitive" not in clipped["log"]
    assert clipped["truncated"]


def test_source_private_key_spanning_excerpt_is_not_leaked(project):
    path = source(project, text="key = '''-----BEGIN PRIVATE KEY-----\n" + "ULTRAPRIVATE\n" * 10 + "-----END PRIVATE KEY-----'''\nraise ValueError('failed')\n")
    result = prepare_evidence(f'  File "{path}", line 7\nValueError: failed', project)
    assert result["references"]
    assert "ULTRAPRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("log", ["", "10 passed in 0.1s", "0 failed, 10 passed", "0 errors", "[failure log truncated]", "Traceback (most recent call last):\n  File \"missing.py\", line 1"])
def test_passing_generic_or_incomplete_logs_do_not_assert_failures(log):
    assert not has_failure_signal(log)
    assert not any(signal["kind"] == "failure" for signal in prepare_evidence(log)["signals"])


@pytest.mark.parametrize("log", ["AssertionError: bad", "ValueError: invalid", "TypeError: invalid", "Error: broken", "FAILED tests/test_a.py::test_case", "FAIL src/main.test.ts", "E   assert 1 == 2", "1 failed, 5 passed", "2 errors in collection"])
def test_observed_failure_recognizer(log):
    assert has_failure_signal(log)


def test_missing_non_source_and_out_of_range_refs_are_not_invented(project):
    source(project, "available.py", "one line\n")
    source(project, "notes.txt", "not source\n")
    result = prepare_evidence('  File "missing.py", line 1\n  File "notes.txt", line 1\n  File "available.py", line 99\nREADME suggests available.py\nValueError: bad', project)
    assert result["references"] == []


def test_bounds_include_json_escaping_and_signal_line_integrity(project):
    for index in range(6):
        source(project, f"src/file{index}.py", "value = '" + "界" * 3000 + "'\n")
    log = "\n".join(f'  File "src/file{index}.py", line 1' for index in range(6))
    log += "\nValueError: " + "界\\\"" * 20_000
    result = prepare_evidence(log, project, ["界" * 20_000] * 50)
    assert len(result["log"]) <= 12_000
    assert len(json.dumps(result)) <= 24_000
    assert len(result["references"]) <= 3
    assert all(len(row["excerpt"]) <= 1200 for row in result["references"])
    assert result["truncated"]
    for signal in result["signals"]:
        assert signal["text"] == result["log"].splitlines()[signal["line"] - 1]


def test_retains_failure_after_long_traceback_and_strips_terminal_controls():
    log = "\x1b[31mTraceback (most recent call last):\x1b[0m\r\n" + '\n'.join(f'  File "file{i}.py", line 1' for i in range(30)) + "\nValueError: bad\x00"
    result = prepare_evidence(log)
    assert any(row["kind"] == "failure" for row in result["signals"])
    assert "\x1b" not in result["log"] and "\x00" not in result["log"]
    assert result["truncated"]


def test_no_scanning_subprocess_or_network(project, monkeypatch):
    source(project)

    def forbidden(*args, **kwargs):
        raise AssertionError("evidence preparation must not scan, execute, or connect")

    monkeypatch.setattr(Path, "rglob", forbidden)
    monkeypatch.setattr(Path, "glob", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    assert prepare_evidence('  File "src/example.py", line 2\nValueError: failed', project)["references"]


def test_huge_numeric_locations_are_rejected_without_integer_conversion_failure(project):
    digits = "9" * 6000
    result = prepare_evidence(f'  File "src/example.py", line {digits}\n{digits} failed', project)
    assert result["references"] == []
    assert not has_failure_signal(result["log"])


@pytest.mark.parametrize("args", [(None,), ("log", "project"), ("log", None, "pytest"), ("log", None, [1])])
def test_rejects_unsupported_input_types(args):
    with pytest.raises(TypeError):
        prepare_evidence(*args)
