"""Standalone trusted-test worker; invoked by path, without importing decilist.

Keeping this standalone permits testing a pinned project copy which predates
the V4 package. The trace plugin is loaded by path under a unique module name.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys


class _Audit:
    def __init__(self, output: Path):
        self.output = output
        self.data = {"collected": [], "reports": [], "collection_errors": []}

    def pytest_collection_finish(self, session):
        self.data["collected"] = [item.nodeid for item in session.items]

    def pytest_collectreport(self, report):
        if report.failed:
            self.data["collection_errors"].append(report.nodeid)

    def pytest_runtest_logreport(self, report):
        self.data["reports"].append({"nodeid": report.nodeid, "when": report.when, "outcome": report.outcome})

    def pytest_sessionfinish(self, session, exitstatus):
        self.data["exitstatus"] = int(exitstatus)
        self.output.write_text(json.dumps(self.data, sort_keys=True), encoding="utf-8")


def main() -> int:
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    root = Path(request["root"]).resolve()
    # -I excludes ambient PYTHONPATH and the worker's containing directory.
    # Put copied implementation roots first, including the pinned vendor tree.
    copied_paths = [root / "vendor", root / "packages" / "python" / "src", root / "src", root]
    sys.path[:0] = [str(path) for path in copied_paths if path.is_dir()]
    os.environ["DECILIST_TRACE_OUTPUT"] = request["trace_output"]
    os.environ["DECILIST_TRACE_ROOT"] = str(root)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    if request.get("capture_failure_text") is True:
        os.environ["DECILIST_TRACE_FAILURE_TEXT"] = "1"
    else:
        os.environ.pop("DECILIST_TRACE_FAILURE_TEXT", None)
    sys.dont_write_bytecode = True
    plugin_spec = importlib.util.spec_from_file_location("_decilist_v4_owned_trace", request["trace_plugin"])
    if plugin_spec is None or plugin_spec.loader is None:
        raise RuntimeError("V4 trace plugin is unavailable")
    trace_plugin = importlib.util.module_from_spec(plugin_spec)
    sys.modules[plugin_spec.name] = trace_plugin
    plugin_spec.loader.exec_module(trace_plugin)
    import pytest
    import pytest_asyncio.plugin
    audit = _Audit(Path(request["audit_output"]))
    controls = ["--rootdir=" + str(root), "--confcutdir=" + str(root), "--basetemp=" + request["basetemp"], "-p", "no:cacheprovider", "-o", "addopts="]
    return int(pytest.main([*request["pytest_args"], *controls], plugins=[trace_plugin, audit, pytest_asyncio.plugin]))


if __name__ == "__main__":
    raise SystemExit(main())
