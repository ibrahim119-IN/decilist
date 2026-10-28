"""Explicit one-time configuration of an already running local V6 model.

This command discovers identity and renders the fixed mode probe. It never
requests completion, downloads weights, starts a service, or switches profiles.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import stat
import sys

import httpx

from .llama_cpp import LlamaCppProvider, _MAX_PROMPT, _origin, _text
from .triage_hypothesis_v6 import (GENERATION_PROFILES, PINNED_BUILD, V6LlamaCppProvider,
                                  verify_generation_profile)

__all__ = ["discover_config", "setup"]
_IDENTITY_FIELDS = ("model", "build_info", "model_path", "chat_template_sha256", "n_vocab", "n_ctx", "n_ctx_train")


class SetupError(RuntimeError):
    """Setup could not safely validate or save a new local configuration."""


class _DiscoveryHTTP(LlamaCppProvider):
    """Use the existing bounded local HTTP decoder before the alias is known.

    The placeholder is never transmitted to the server: this utility exposes
    only a native GET /props. Actual identity/mode checks use the V6 provider.
    """

    def __init__(self, *, base_url: str, transport: httpx.AsyncBaseTransport | None):
        super().__init__(model="local-identity-discovery", expected_build=PINNED_BUILD,
                         base_url=base_url, timeout_seconds=10, transport=transport)

    async def props(self) -> dict:
        return await self._json("GET", "/props")


@dataclass(frozen=True)
class _Output:
    path: Path
    parents: tuple[tuple[Path, int, int], ...]


def _parent_snapshot(path: Path) -> tuple[tuple[Path, int, int], ...]:
    rows = []
    for parent in reversed(path.parent.parents):
        rows.append(parent)
    rows.append(path.parent)
    result = []
    for parent in rows:
        try:
            observed = os.lstat(parent)
        except OSError as exc:
            raise SetupError("The output parent must already exist as a local directory.") from exc
        if (not stat.S_ISDIR(observed.st_mode) or stat.S_ISLNK(observed.st_mode)
                or getattr(observed, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise SetupError("Output parent links, junctions, and reparse points are not accepted.")
        result.append((parent, observed.st_dev, observed.st_ino))
    return tuple(result)


def _new_output(output: Path | str) -> _Output:
    raw = os.fspath(output)
    if not isinstance(raw, str) or not raw or raw.startswith(("\\\\", "//")) or "://" in raw:
        raise SetupError("Choose a new ordinary local output file, not a network or device path.")
    windows = PureWindowsPath(raw)
    path = Path(raw)
    if (windows.drive and not windows.root or ".." in path.parts or not path.name
            or any(":" in part or part.endswith((".", " ")) or PureWindowsPath(part).is_reserved()
                   for part in path.parts if part != path.anchor)):
        raise SetupError("Output paths must not use traversal, drive-relative names, device names, or alternate streams.")
    path = path.absolute()
    parents = _parent_snapshot(path)
    try:
        os.lstat(path)
    except FileNotFoundError:
        pass
    else:
        raise FileExistsError("Output already exists; choose a new file. Existing configurations are never overwritten.")
    return _Output(path, parents)


def _save(config: dict, output: _Output) -> None:
    if _parent_snapshot(output.path) != output.parents:
        raise SetupError("The output parent changed during verification; no configuration was written.")
    data = (json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(output.path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        target.write(data)
        target.flush()
        os.fsync(target.fileno())


async def discover_config(url: str, profile: str, *, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """Discover and verify one explicit profile without generating a model token.

    ``transport`` is a trusted test hook. Default HTTP uses the existing numeric
    loopback, bounded-JSON, no-redirect, and no-environment-proxy guards. HTTP
    identity is not cryptographic weights or launch-flag attestation.
    """
    if not isinstance(profile, str) or profile not in GENERATION_PROFILES:
        raise SetupError("Select an explicit supported V6 generation profile; automatic profile selection is disabled.")
    origin = _origin(url)
    async with _DiscoveryHTTP(base_url=origin, transport=transport) as discovery:
        props = await discovery.props()
        if props.get("build_info") != PINNED_BUILD or props.get("is_sleeping") is not False:
            raise SetupError("The native local runtime must be ready and use the pinned V6 build.")
        config = {
            "model": _text(props.get("model_alias"), "model alias"),
            "expected_build": PINNED_BUILD,
            "generation_profile": profile,
            "expected_chat_template_sha256": hashlib.sha256(_text(props.get("chat_template"), "chat template", _MAX_PROMPT).encode("utf-8")).hexdigest(),
            "expected_model_path": _text(props.get("model_path"), "model path", 4096),
        }
        async with V6LlamaCppProvider(base_url=origin, timeout_seconds=10, transport=transport, **config) as provider:
            verified = await verify_generation_profile(provider)
            final = await provider.ready()
            if (verified.get("generation_performed") is not False
                    or any(final.get(key) != verified["runtime"].get(key) for key in _IDENTITY_FIELDS)):
                raise SetupError("The local runtime identity changed during native profile verification.")
        return config


async def setup(url: str, profile: str, output: Path | str, *, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """Verify the selected local runtime and create exactly one new UTF-8/LF config."""
    target = _new_output(output)
    config = await discover_config(url, profile, transport=transport)
    _save(config, target)
    return config


def _url_argument(value: str) -> str:
    try:
        return _origin(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", type=_url_argument, default="http://127.0.0.1:8792")
    parser.add_argument("--profile", choices=tuple(GENERATION_PROFILES), required=True)
    parser.add_argument("--output", type=Path, required=True, help="New local file; parent must already exist without links")
    args = parser.parse_args(argv)
    try:
        asyncio.run(setup(args.url, args.profile, args.output))
    except (SetupError, OSError, ValueError, RuntimeError) as exc:
        print(f"Local model setup failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    print(f"Saved validated local model configuration to {args.output}. No generation was requested.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
