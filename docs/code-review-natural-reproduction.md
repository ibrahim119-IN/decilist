# Reproduce the historical review experiment

This is a pinned, exposed benchmark, not a new holdout on rerun. Read the [protocol](code-review-natural-protocol.md), [results](code-review-natural-results.md), and [licenses](../THIRD_PARTY.md). All paths below use drive L on the original Windows host. Use an empty new root; never overwrite the retained first trial.

Prerequisites: Python with `httpx`, Node 22.23.1, npm, and the pinned lint workspace (`npm ci --ignore-scripts` in `benchmarks/code-review-lint`). Historical source download and dependency installation require network access. No install scripts are executed. The protected probes are bounded adaptations, not the complete upstream suites.

```powershell
$env:TEMP = $env:TMP = 'L:\CodexData\Decilist\tmp'
$env:PIP_CACHE_DIR = 'L:\CodexData\Decilist\cache\pip'
$env:npm_config_cache = 'L:\CodexData\Decilist\cache\npm'
$env:PYTHONDONTWRITEBYTECODE = '1'
$trialRoot = 'L:\CodexData\Decilist\work\code-review-natural-replay'
python benchmarks/prepare_code_review_natural.py --root $trialRoot
```

Before installation, copy each retained `reports/code-review-natural-01/dependency-locks/Nxx/package-lock.json` into `$trialRoot/Nxx/package-lock.json`. The preparation helper keeps an existing lock; then run:

```powershell
python benchmarks/prepare_code_review_natural.py --root $trialRoot --install
node benchmarks/code-review-lint/natural.mjs $trialRoot
```

The `sources.json` inventory records original bytes and dependency lock hashes. Compare them with the retained first-run inventory; changed dependencies constitute a different environment. Downloaded source includes original MIT licenses. No model receives the catalog titles, patches, fixed revisions or protected tests.

Start the already installed optional review specialist on loopback port 8766 as described in [code-review.md](code-review.md), using GPU1. The experiment's second local endpoint is llama.cpp b11889, commit `d854d98bae598388ecea1ed84396587e5edc326d`, serving Qwen3-Coder-30B-A3B-Instruct Q4_K_M on GPU0 with these arguments:

```text
--model <cached-Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf>
--alias qwen3-coder-30b-a3b-q4km --host 127.0.0.1 --port 8792
--ctx-size 16384 --parallel 1 --n-gpu-layers 99 --split-mode none
--main-gpu 0 --fit off --batch-size 512 --ubatch-size 128
--flash-attn on --jinja --offline --no-context-shift --no-webui
```

Set `CUDA_VISIBLE_DEVICES=0` for the Qwen server. Both local endpoints are required; using a different model or runtime is a new comparison. There is no cloud fallback. Record actual SHA-256 hashes into `$trialRoot/runtime-hashes.json` with keys `model` and `runtime`. The runner checks them against:

```json
{
  "model": "f6050b4da22869aa3fb9d94bb3314e9fc5a027d4741c1f97761c387d6b2b2335",
  "runtime": "049243ee4986362abecbe70b1a2ac1457d01bc4dc6178f3a3a46d9d199f57022"
}
```

Do not copy those values without hashing the actual files. See [retained runtime profile](../reports/code-review-natural-01/preflight.json). The GGUF publisher revision was `0ca51122012fe4a20dc82988ee58780b008ce4e3`. Download and startup time are not included in timed tasks.

```powershell
python benchmarks/run_code_review_natural.py --root $trialRoot --preflight
python benchmarks/run_code_review_natural.py --root $trialRoot --freeze
python benchmarks/run_code_review_natural.py --root $trialRoot --run
```

Preflight must observe eight parent failures and eight fixed passes. It warms both models only on neutral examples. Freeze is write-once. Run checks the frozen code and artifacts; completed trials can be read on resume, but an interrupted trial cannot silently be replaced. It writes every packet, model request, response, applied diff and verification result. No edits touch historical originals or the Decilist source tree. Exact edits are limited to the supplied production paths in disposable copies; this is a boundary check, not a security sandbox for arbitrary hostile code.

Post-trial checks are deliberately separate. For example:

```powershell
node --require ./benchmarks/code_review_no_external.cjs benchmarks/code_review_control_checks.mjs N02 "$trialRoot/N02/fixed"
node --require ./benchmarks/code_review_no_external.cjs benchmarks/code_review_control_checks.mjs N02 "$trialRoot/N02/trials/N02-broken-assisted/project"
```

The first passes on the original study; the second fails because the proposed repair changes `AbortError`. Checks also support N01, N03, N04, N07 and N08. Bound each process to 15 seconds when automating them. Their original outputs are retained; they must not be used as hidden feedback in a supposedly fresh replay.

`analyze_code_review_natural.py` exports the **first study** with its reviewed adjudication file. That file contains evaluator-authored judgments about these exact captured responses; do not reuse its labels for new responses or another study. Raw evidence totals about 2.4 MB and contains only public source/project-local paths, with original licenses retained. The original first-run freeze, prompts and harness remain unchanged after exposure.
