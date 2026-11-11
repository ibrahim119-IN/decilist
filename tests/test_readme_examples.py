import re
from pathlib import Path

from examples import cost_aware, named_decision, own_model, quickstart, verified_choice

REPO = Path(__file__).resolve().parents[1]


async def test_quickstart_prints_the_decision_shown_in_the_readme(capsys):
    await quickstart.main()
    assert capsys.readouterr().out.strip() == "Do this: refund"


async def test_own_model_example_accepts_and_abstains():
    assert (await own_model.handle(3)).startswith("urgent (confidence 0.90, from stand-in-v1)")
    assert (await own_model.handle(0)).startswith("normal (confidence 0.90, from stand-in-v1)")
    assert await own_model.handle(1) == "abstained: ask a person (attempt: low_confidence)"


async def test_verified_choice_example(capsys):
    await verified_choice.main()
    assert capsys.readouterr().out.strip().splitlines() == [
        "The provider is a fixed stand-in, not a real model.", "verified: backup"
    ]


async def test_named_decision_example(capsys):
    await named_decision.main()
    assert capsys.readouterr().out.strip().splitlines() == [
        "The provider is a fixed stand-in, not a real model.",
        "cpu_high: ignore", "service_down: auto_remediate", "failed_logins: page_oncall", "disk_latency: ask an analyst",
        "alert_triage at 0.70: answered 3 of 4, 3 of 3 correct",
        "decisions in the log: alert_repeat, alert_triage",
    ]


async def test_cost_aware_example_matches_the_guide(capsys):
    await cost_aware.main()
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[2] == "alert 2: most probable ignore (0.75), cost rule: ask an analyst"
    assert lines[5] == "alert 5: most probable ignore (0.50), cost rule: page_oncall"
    assert lines[7:9] == ["cost rule: total cost 4, wrong 0, asked a person 2", "confidence 0.70: total cost 102, wrong 1, asked a person 1"]
    # The worked example in docs/cost-aware-decisions.md and the README uses this table and these probabilities.
    choice, expected = cost_aware.COSTS.pick({"ignore": .75, "auto_remediate": .05, "page_oncall": .20})
    assert choice is None and {k: round(v, 2) for k, v in expected.items()} == {"ignore": 20.25, "auto_remediate": 8.75, "page_oncall": 2.4}
    guide = (REPO / "docs/cost-aware-decisions.md").read_text(encoding="utf-8")
    assert all(text in guide for text in ("= 20.25", "= 8.75", "= 2.40", "102.00", "Replayed 6 of 6 decisions"))


def test_docs_local_links_exist():
    for name in ("capabilities.md", "named-decisions.md", "cost-aware-decisions.md"):
        text = (REPO / "docs" / name).read_text(encoding="utf-8")
        targets = re.findall(r"\]\(((?!https?://|#)[^)]+)\)", text)
        assert targets
        missing = [target for target in targets if not (REPO / "docs" / target.split("#")[0]).resolve().exists()]
        assert not missing, (name, missing)


def test_readme_first_program_matches_the_example():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    program = re.search(r"Save this as `app.py`:\n\n```python\n(.*?)```", readme, re.S).group(1)
    example = (REPO / "examples/quickstart.py").read_text(encoding="utf-8")
    assert program.strip().removesuffix("asyncio.run(main())") in example


def test_readme_images_and_local_links_exist():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    targets = re.findall(r'(?:\]\(|src=")((?!https?://|#|<)[^)"]+)', readme)
    assert targets
    missing = [target for target in targets if not (REPO / target.split("#")[0]).exists()]
    assert not missing
