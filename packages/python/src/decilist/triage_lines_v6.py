"""Selected line2 V6 workflow; exact canonical verification remains shared."""
from __future__ import annotations

from pathlib import Path

from . import triage_v6 as legacy_workflow
from . import triage_line_hypothesis_v6 as selected_generator


VERSION = legacy_workflow.VERSION + "+" + selected_generator.VERSION
SUPPORTED = legacy_workflow.SUPPORTED
DEFAULT_MAX_CONTEXT_CHARS = legacy_workflow.DEFAULT_MAX_CONTEXT_CHARS
load_model_config = legacy_workflow.load_model_config
format_text = legacy_workflow.format_text


async def run_triage_v6(*args, **kwargs):
    """Allocate isolated feedback state for this workflow's bounded attempts."""
    if "_generator" in kwargs:
        raise TypeError("The selected line workflow owns its per-run generator")
    adapter = selected_generator.LineEditGenerator()
    result = await legacy_workflow.run_triage_v6(*args, **kwargs, _generator=adapter.generate_hypothesis)
    result["workflow_version"] = VERSION
    result["edit_protocol"] = selected_generator.VERSION
    result["generation_module"] = selected_generator.__name__
    result["generation_adapter_path"] = str(Path(selected_generator.__file__).resolve())
    result["generation_specification"] = adapter.specification()
    if result.get("report_path"):
        legacy_workflow._write_json(Path(result["report_path"]), result)
    return result


def main(argv=None):
    return legacy_workflow.main(argv, _workflow=run_triage_v6)


if __name__ == "__main__":
    raise SystemExit(main())
