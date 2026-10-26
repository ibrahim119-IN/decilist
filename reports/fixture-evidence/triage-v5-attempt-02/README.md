# Original V5 fixture proof

From a fresh checkout, install the normal development dependencies, then run:

```sh
python scripts/restore_triage_v5_fixture_evidence.py
python -m pytest -q
```

The restore command uses the frozen lab's normal destination outside the checkout. Set `DECILIST_TRIAGE_V5_WORK_ROOT` before both commands to choose another location. CI does this with a temporary directory. No model runtime is needed for restoring evidence or running the fixture tests.

`original-proof.zip` contains the exact five original files from the successful `attempt-02-all` authoring run. ZIP preserves their original bytes and line endings across Git checkouts. `manifest.json` records the archive and member hashes. The restore script independently binds every member to the unchanged V5 corpus freeze, including the original proof hashes and lab/catalog snapshot identities. It never regenerates proof, executes archived source, or overwrites a conflicting artifact. Repeating the command accepts identical files.

These are historical fixture observations, not fresh CI test results. The subsequent ordinary pytest command still executes the actual fixture tests. Neither frozen V5 source nor its assertions or freeze hashes were changed to fix portability.

All five originals were scanned as raw bytes, decoded JSON keys/string values, raw source and test logs before packaging. No absolute Windows paths, private POSIX paths, emails, private keys or known service tokens were found. Two credential-keyword matches were manually identified as the Python traceback fragments `auth=auth` and `auth` followed by `response`. URLs are public source/license links or synthetic `.test`/`.invalid` hosts, without URL credentials or secret query parameters. The snapshots contain already-published V5 fixture definitions; there are no model requests, model outputs or human study records. This review is specific to these original bytes and is not a general secret-detection guarantee.
