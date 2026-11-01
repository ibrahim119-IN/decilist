import re
from pathlib import Path

from examples import own_model, quickstart

REPO = Path(__file__).resolve().parents[1]


async def test_quickstart_prints_the_decision_shown_in_the_readme(capsys):
    await quickstart.main()
    assert capsys.readouterr().out.strip() == "Do this: refund"


async def test_own_model_example_accepts_and_abstains():
    assert (await own_model.handle(3)).startswith("urgent (confidence 0.90, from stand-in-v1)")
    assert (await own_model.handle(0)).startswith("normal (confidence 0.90, from stand-in-v1)")
    assert await own_model.handle(1) == "abstained: ask a person (attempt: low_confidence)"


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
