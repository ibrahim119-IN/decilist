"""Synthetic ledger writes only; no real agent or natural trial is opened."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks import triage_agent_trial as trial


def test_write_round_trips_utf8_after_syncing_before_replace(tmp_path, monkeypatch):
    original = b'{"status": "previous"}\n'
    record = tmp_path / "trial.json"
    record.write_bytes(original)
    state = {"status": "active", "observation": "Caf\u00e9 \u0645\u0644\u0627\u062d\u0638\u0629", "attempts": [1, None]}
    synced = []
    fsync, replace = trial.os.fsync, trial.os.replace

    def sync(fd):
        temporary, = tmp_path.glob(".trial-*.tmp")
        data = temporary.read_bytes()
        assert record.read_bytes() == original
        assert json.loads(data.decode("utf-8")) == state
        assert data.endswith(b"\n")
        fsync(fd)
        synced.append(temporary)

    def replace_after_sync(source, destination):
        assert synced == [source]
        assert source.parent == destination.parent == tmp_path
        assert destination == record
        replace(source, destination)

    monkeypatch.setattr(trial.os, "fsync", sync)
    monkeypatch.setattr(trial.os, "replace", replace_after_sync)
    trial._write(tmp_path, state)

    data = record.read_bytes()
    assert json.loads(data.decode("utf-8")) == state
    assert "Caf\u00e9".encode("utf-8") in data
    assert not list(tmp_path.glob(".trial-*.tmp"))


@pytest.mark.parametrize("invalid", [object(), float("nan")])
def test_serialization_failure_preserves_old_record_and_cleans_own_temp(tmp_path, invalid):
    record = tmp_path / "trial.json"
    original = b'{"status": "previous"}\n'
    record.write_bytes(original)
    unrelated = tmp_path / ".trial-another-writer.tmp"
    unrelated.write_bytes(b"another writer's data")

    with pytest.raises((TypeError, ValueError)):
        trial._write(tmp_path, {"serializable_prefix": [1, 2, 3], "invalid": invalid})

    assert record.read_bytes() == original
    assert unrelated.read_bytes() == b"another writer's data"
    assert list(tmp_path.glob(".trial-*.tmp")) == [unrelated]


@pytest.mark.parametrize("operation", ["write", "flush", "fsync", "replace"])
@pytest.mark.parametrize("failure_type", [OSError, KeyboardInterrupt])
def test_io_failure_preserves_old_record_and_cleans_own_temp(tmp_path, monkeypatch, operation, failure_type):
    record = tmp_path / "trial.json"
    original = b'{"status": "previous"}\n'
    record.write_bytes(original)
    unrelated = tmp_path / ".trial-another-writer.tmp"
    unrelated.write_bytes(b"another writer's data")
    failure = failure_type(f"synthetic {operation} failure")
    path_open = Path.open

    class FailingOutput:
        def __init__(self, output):
            self.output = output

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.output.__exit__(*args)

        def write(self, text):
            if operation == "write":
                self.output.write(text[:1])
                raise failure
            return self.output.write(text)

        def flush(self):
            if operation == "flush":
                raise failure
            return self.output.flush()

        def fileno(self):
            return self.output.fileno()

    def open_output(path, *args, **kwargs):
        output = path_open(path, *args, **kwargs)
        if args and args[0] == "x" and path.parent == tmp_path:
            return FailingOutput(output)
        return output

    def fail(*args):
        raise failure

    monkeypatch.setattr(Path, "open", open_output)
    if operation in {"fsync", "replace"}:
        monkeypatch.setattr(trial.os, operation, fail)

    with pytest.raises(failure_type) as caught:
        trial._write(tmp_path, {"status": "active"})

    assert caught.value is failure
    assert record.read_bytes() == original
    assert unrelated.read_bytes() == b"another writer's data"
    assert list(tmp_path.glob(".trial-*.tmp")) == [unrelated]


def test_existing_temporary_path_is_never_removed(tmp_path, monkeypatch):
    record = tmp_path / "trial.json"
    original = b'{"status": "previous"}\n'
    record.write_bytes(original)
    existing = tmp_path / ".trial-existing.tmp"
    existing.write_bytes(b"another writer's data")
    monkeypatch.setattr(trial, "uuid4", lambda: SimpleNamespace(hex="existing"))

    with pytest.raises(FileExistsError):
        trial._write(tmp_path, {"status": "active"})

    assert record.read_bytes() == original
    assert existing.read_bytes() == b"another writer's data"


def test_cleanup_failure_does_not_hide_original_write_failure(tmp_path, monkeypatch):
    record = tmp_path / "trial.json"
    original = b'{"status": "previous"}\n'
    record.write_bytes(original)
    failure = OSError("synthetic fsync failure")
    unlink = Path.unlink

    def fail_sync(fd):
        raise failure

    def fail_temp_cleanup(path, *args, **kwargs):
        if path.parent == tmp_path and path.name.startswith(".trial-"):
            raise PermissionError("synthetic cleanup failure")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(trial.os, "fsync", fail_sync)
    monkeypatch.setattr(Path, "unlink", fail_temp_cleanup)

    with pytest.raises(OSError) as caught:
        trial._write(tmp_path, {"status": "active"})

    assert caught.value is failure
    assert record.read_bytes() == original
    assert len(list(tmp_path.glob(".trial-*.tmp"))) == 1
