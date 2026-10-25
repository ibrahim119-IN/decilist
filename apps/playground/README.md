# Optional local UI

The canonical Playground and ordinary-app example ship in the Python package:

- `packages/python/src/decilist/static/`: HTML, JavaScript, and CSS.
- `packages/python/src/decilist/web.py`: optional routes and app-owned request mapping.
- `packages/python/src/decilist/demo.py`: explicitly labelled authored rules.

Both `decilist runtime start --provider demo` and the repository's `apps.api.main` entry point serve the same assets at `/` and `/demo`. No Playground visit is required to use the SDK.
