import asyncio
import copy
import json
import sys
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from decilist import Client, CodeReviewer, LiquidCodeReviewProvider, ReviewPolicy, ReviewResult
from decilist.code_review import DEFAULT_RULES, ReviewRequest, review_questions
from decilist.server import create_app


class FakeReview:
    model, revision, locality = "test-specialist", "frozen", "local"

    def __init__(self, scores=None):
        self.calls = []
        self.scores = scores or {}

    async def review(self, state, questions):
        self.calls.append((copy.deepcopy(state), copy.deepcopy(questions)))
        return {"answers": {name: {"type": "noul", "noul": self.scores.get(name, 0.01)} for name in questions}}


async def test_one_call_uses_trained_questions_and_does_not_invent_precise_lines():
    provider = FakeReview({"error_handling_problem": 0.95})
    result = await CodeReviewer(provider).review("try { run(); } catch {}", task="Run the task and propagate failures.", language="JavaScript")
    assert result.status == "flagged" and result.advisory
    assert result.findings[0].rule == "error_handling_problem"
    assert len(provider.calls) == 1
    state, questions = provider.calls[0]
    assert state == {"task": "Run the task and propagate failures.", "language": "JavaScript", "code": "try { run(); } catch {}"}
    assert list(questions) == list(DEFAULT_RULES)
    assert "local catch is not required" in questions["error_handling_problem"]["criteria"]["true"]
    assert "noise_comments" not in questions
    assert result.reviewed_lines == result.total_lines == 1


@pytest.mark.parametrize("line_count, expected", [(1, 1), (80, 1), (81, 2), (148, 2), (149, 3), (2000, 30)])
async def test_complete_window_coverage_including_boundary(line_count, expected):
    provider = FakeReview()
    code = "\r\n".join(f"const field{i} = {i};" for i in range(line_count)) + "\r\n"
    result = await CodeReviewer(provider).review(code, task="Define fields.")
    assert result.status == "no_flags"
    assert result.reviewed_lines == result.total_lines == line_count
    assert len(result.windows) == expected
    assert all(len(state["code"].splitlines()) <= 80 for state, _ in provider.calls)
    assert provider.calls[-1][0]["code"].endswith(f"const field{line_count - 1} = {line_count - 1};")


@pytest.mark.parametrize("invalid", [True, "0.9", float("nan"), float("inf"), -0.1, 1.1, None])
async def test_invalid_probability_never_looks_clean(invalid):
    result = await CodeReviewer(FakeReview({"unsafe_input": invalid})).review("input();", task="Validate input.")
    assert result.status == "unavailable" and result.error == "invalid_output"
    assert result.reviewed_lines == 0 and result.findings == []


@pytest.mark.parametrize("response", [{}, {"answers": {}}, {"answers": []}, {"answers": {"extra": {"type": "noul", "noul": 0}}}])
async def test_missing_or_wrong_answers_are_unavailable(response):
    class Broken(FakeReview):
        async def review(self, *_):
            return response
    assert (await CodeReviewer(Broken()).review("const valid = true;", task="Define a flag.")).status == "unavailable"


async def test_partial_review_retains_flags_and_reports_unavailable():
    class Partial(FakeReview):
        async def review(self, *args):
            if self.calls:
                raise RuntimeError("private source must not appear in response")
            return await super().review(*args)
    result = await CodeReviewer(Partial({"unsafe_input": 0.95})).review("call();\n" * 81, task="Call a function.")
    assert result.status == "unavailable" and result.error == "provider_error"
    assert result.reviewed_lines == 80 and len(result.findings) == 1
    assert "private source" not in result.model_dump_json()


async def test_timeout_is_not_a_pass():
    class Slow(FakeReview):
        async def review(self, *_):
            await asyncio.sleep(1)
    result = await CodeReviewer(Slow(), ReviewPolicy(timeout_ms=1)).review("call();", task="Call a function.")
    assert result.status == "unavailable" and result.error == "timeout"


async def test_native_review_timeout_does_not_overlap_next_gpu_call(monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []
    class Model:
        def system_one(self, *_):
            calls.append(1)
            started.set()
            release.wait(5)
            return {"answers": {name: {"type": "noul", "noul": 0.01} for name in DEFAULT_RULES}}
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(inference_mode=nullcontext))
    provider = LiquidCodeReviewProvider(Model())
    reviewer = CodeReviewer(provider, ReviewPolicy(timeout_ms=50))
    try:
        first = asyncio.create_task(reviewer.review("call();", task="Call a function."))
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.001)
        assert started.is_set()
        assert (await first).error == "timeout"
        assert (await reviewer.review("call();", task="Call a function.")).error == "timeout"
        assert len(calls) == 1
    finally:
        release.set()
        while provider._running:
            await asyncio.sleep(0.001)
    assert (await reviewer.review("call();", task="Call a function.")).status == "no_flags"


@pytest.mark.parametrize("update", [{"language": "Python"}, {"code": "  "}, {"code": "x\0y"}, {"code": "x\n" * 2001}, {"task": ""}, {"code": "x" * 65537}])
def test_unsupported_input_fails_before_inference(update):
    with pytest.raises(ValidationError):
        ReviewRequest(**({"code": "call();", "task": "Call a function."} | update))


def test_remote_provider_rejected_and_license_opt_in_preserved():
    provider = FakeReview()
    provider.locality = "remote"
    with pytest.raises(ValueError, match="local provider"):
        CodeReviewer(provider)
    with pytest.raises(ValueError, match="allow_model_code"):
        CodeReviewer.load()
    with pytest.raises(ValidationError):
        ReviewPolicy(rules=["unsafe_input", "unsafe_input"])


async def test_forged_clean_result_rejected():
    good = (await CodeReviewer(FakeReview()).review("call();", task="Call a function.")).model_dump()
    for change in ({"reviewed_lines": 0}, {"windows": []}, {"status": "flagged"}, {"error": "timeout"}):
        with pytest.raises(ValidationError):
            ReviewResult.model_validate(good | change)


async def test_redundant_completed_window_is_rejected():
    good = (await CodeReviewer(FakeReview()).review("call();\n" * 80, task="Call functions.")).model_dump()
    good["windows"].append({**good["windows"][0], "start_line": 69})
    with pytest.raises(ValidationError):
        ReviewResult.model_validate(good)


def test_cli_errors_are_bounded_and_do_not_echo_private_input(tmp_path, capsys):
    from decilist.cli import main
    source = tmp_path / "invalid.ts"
    source.write_text("private source\0", encoding="utf-8")
    assert main(["review", "check", str(source), "--task", "Review this."]) == 2
    assert "private source" not in capsys.readouterr().err
    assert main(["review", "check", str(tmp_path / "missing.ts"), "--task", "Review this."]) == 2
    assert "FileNotFoundError" in capsys.readouterr().err


async def test_python_client_and_server_round_trip_and_boundary():
    provider = FakeReview({"type_safety_hole": 0.99})
    app = create_app(reviewer=CodeReviewer(provider), include_ui=False)
    async with Client("http://127.0.0.1") as client:
        await client._http.aclose()
        client._http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")
        result = await client.review("const parsed: any = read();", task="Read a validated record.")
        assert result.status == "flagged" and result.findings[0].rule == "type_safety_hole"
        body = {"code": "call();", "task": "Call a function."}
        response = await client._http.post("/v1/code-review", json=body, headers={"origin": "https://foreign.example"})
        assert response.status_code == 403
        assert len(provider.calls) == 1
        assert (await client._http.post("/v1/code-review", json=body | {"language": "Python"})).status_code == 422
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(include_ui=False)), base_url="http://127.0.0.1") as client:
        assert (await client.post("/v1/code-review", json=body)).status_code == 503


def test_cli_help_and_setup_require_explicit_download(capsys):
    from decilist.cli import main
    from decilist.code_review_cli import main as review_main
    with pytest.raises(SystemExit) as caught:
        main(["review", "--help"])
    assert caught.value.code == 0 and "setup" in capsys.readouterr().out
    with pytest.raises(SystemExit) as caught:
        review_main(["setup"])
    assert caught.value.code == 2
