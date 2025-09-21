# Release and versioning guide

## Version inventory

All runtime components and packages derive their version from the shared
[`VERSION`](../VERSION) file:

| Component | Source |
| --- | --- |
| Control-plane Python package | `control-plane/pyproject.toml` uses `../VERSION` via `tool.setuptools.dynamic`. |
| Worker Python package | `worker/pyproject.toml` uses `../VERSION` via `tool.setuptools.dynamic`. |
| Runner Python package | `runner/pyproject.toml` uses `../VERSION` via `tool.setuptools.dynamic`. |
| FastAPI apps | `shared.version.__version__` is imported by each `create_app` factory. |
| Shared Python utilities | `shared/version.py` exposes the canonical `__version__`. |
| Web UI package | `ui/package.json` `version` field must match the `VERSION` file. |

Before the shared module was introduced the worker advertised `0.2.0` in FastAPI
while packaging metadata and the UI still reported `0.1.0`. The shared source of
truth prevents similar drifts going forward.

## Release workflow

1. Decide on the next semantic version number and update [`VERSION`](../VERSION).
2. Synchronise the UI package metadata by running
   `npm version --no-git-tag-version $(cat VERSION)` inside the `ui/` folder or
   by editing `ui/package.json` (and the generated `package-lock.json`) manually.
3. Run `pytest` from the repository root to execute
   `shared/tests/test_versions.py`, ensuring every consumer reports the new
   version.
4. Run the rest of the test suite as needed and ship the release.

The `shared/tests/test_versions.py` test checks the version file, all Python
packages, the UI package manifest, and the FastAPI application factories. If any
component is missed, the test will fail and point to the stale manifest.
