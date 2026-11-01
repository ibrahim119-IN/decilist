# Recorded results

You don't need this folder to use Decilist. It holds results from recorded runs, and some tests read these files. The [docs index](../docs/README.md) explains what each experiment measured.

## Code review experiments

`code-review-natural-01`, `code-review-proof-02` and `code-review-proof-03` keep only their summaries here: analysis, freeze records and evidence hashes. Where a file is listed in `evidence-hashes.json` but missing from this folder, it was removed on 2 November 2026 because it recorded local folder paths from the machine that ran the experiment. Those byte-pinned files couldn't be edited without breaking their hashes.

The full sets, including requests, responses, trial logs and the `results.json` files removed here, are at commit [`2d641ab`](https://github.com/ibrahim119-IN/decilist/tree/2d641ab/reports). The numbers in the results documents were not changed.

One protocol sentence also changed that day: `docs/triage-v5-experiment.md` no longer names a local work folder. Reports recorded before that date hold the hash of the original text, which is at the same commit.

`reports/local/` is ignored by Git, so use it for your own runs.
