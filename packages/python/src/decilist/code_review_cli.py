"""Explicit setup, local service, and read-only checks for the review specialist."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

from .code_review import CodeReviewer, REVIEW_FILES, REVIEW_MODEL, REVIEW_REVISION, ReviewPolicy, ReviewRequest
from .runtime import _device_arg

WEIGHTS_SHA256 = "7533d57eb430090bf5eb469b41efed4106d06c76e4072f9b5856ae7f7495c778"


def setup(*, cache_dir: str | None = None) -> dict:
    from huggingface_hub import snapshot_download
    snapshot = Path(snapshot_download(REVIEW_MODEL, revision=REVIEW_REVISION, cache_dir=cache_dir,
                    allow_patterns=list(REVIEW_FILES), max_workers=4))
    with (snapshot / "model.safetensors").open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != WEIGHTS_SHA256:
        raise RuntimeError("Downloaded review weights failed SHA-256 verification")
    return {"model": REVIEW_MODEL, "revision": REVIEW_REVISION, "snapshot": str(snapshot), "weights_sha256": digest}


def _main(argv: list[str] | None = None) -> int:
    actual = list(sys.argv[1:] if argv is None else argv)
    if actual[:1] == ["investigate"]:
        from .review_repair_cli import main as investigate
        return investigate(actual[1:])
    parser = argparse.ArgumentParser(prog="decilist review", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("investigate", help="Prove a JS defect and retain an isolated candidate; see investigate --help")
    download = commands.add_parser("setup", help="Download the pinned 6.2 GB optional model")
    download.add_argument("--accept-model-license", action="store_true", help="Accept the upstream LFM Open License v1.0")
    download.add_argument("--cache-dir", help="Hugging Face hub directory; otherwise respects HF_HOME")
    for command in ("serve", "check"):
        sub = commands.add_parser(command)
        sub.add_argument("--device", type=_device_arg, default="cuda")
        sub.add_argument("--allow-model-code", action="store_true", help="Allow the pinned Liquid Python implementation")
        if command == "serve":
            sub.add_argument("--port", type=int, default=8766)
        else:
            sub.add_argument("path", type=Path)
            sub.add_argument("--task", required=True, help="The existing function or file's intended behavior")
            sub.add_argument("--threshold", type=float, default=0.9)
            sub.add_argument("--json", action="store_true", help="Output the structured advisory result")
            sub.add_argument("--url", help="Reuse a running local reviewer instead of loading weights")
    args = parser.parse_args(argv)
    if args.command == "setup":
        if not args.accept_model_license:
            parser.error("read the model LICENSE, then pass --accept-model-license to download")
        print(json.dumps(setup(cache_dir=args.cache_dir), indent=2))
        return 0
    if args.command == "serve":
        if not 1 <= args.port <= 65535:
            parser.error("port must be between 1 and 65535")
        from .engine import Decilist
        from .server import create_app
        import uvicorn
        reviewer = CodeReviewer.load(device=args.device, allow_model_code=args.allow_model_code)
        app = create_app(Decilist([reviewer.provider]), provider="liquid-code-review", include_ui=False, reviewer=reviewer)
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
        return 0
    languages = {".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".jsx": "JSX", ".ts": "TypeScript", ".tsx": "TSX"}
    if args.path.suffix.lower() not in languages:
        parser.error("review supports .js, .mjs, .cjs, .jsx, .ts and .tsx")
    # Bound the read before decoding or loading a model. The file is never executed.
    with args.path.open("rb") as stream:
        content = stream.read(262145)
    if len(content) > 262144:
        parser.error("file is too large for review; review a smaller source module")
    request = ReviewRequest(code=content.decode("utf-8-sig"), task=args.task, language=languages[args.path.suffix.lower()],
                            policy=ReviewPolicy(threshold=args.threshold))

    async def run():
        if args.url:
            from .client import Client
            async with Client(args.url) as client:
                return await client.review(request.code, task=request.task, language=request.language, policy=request.policy)
        reviewer = CodeReviewer.load(device=args.device, allow_model_code=args.allow_model_code)
        return await reviewer.review(request.code, task=request.task, language=request.language, policy=request.policy)

    result = asyncio.run(run())
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print(f"{result.status}: reviewed {result.reviewed_lines}/{result.total_lines} lines ({result.latency_ms:.0f} ms)")
        for finding in result.findings:
            print(f"  {finding.rule}: {finding.probability:.1%}; context lines {finding.start_line}-{finding.end_line}")
        print("Advisory only. No flags is not an approval; keep your tests and normal review.")
    # Findings do not block existing workflows automatically. Unavailability is an error.
    return 2 if result.status == "unavailable" else 0


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        # Validation and provider exceptions may contain private source text.
        print(f"Review failed ({type(exc).__name__}). Check the input, local service, and cached model. "
              "For setup use 'decilist review setup --help'; for local loading pass --allow-model-code.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
