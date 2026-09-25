# Reproducible Environment

## Decision: uv.lock is the source of truth

`pyproject.toml` keeps compatible version *ranges*, which is what development needs. Exact versions live in **`uv.lock`**, a single universal lockfile produced by [uv](https://docs.astral.sh/uv/). It covers:

- every supported Python version (3.11–3.13+);
- every platform, using environment markers;
- SHA-256 hashes for every artifact.

The Python-3.12+-only NautilusTrader pin is represented correctly: uv resolves the `< 3.12` and `>= 3.12` environments separately, and numpy/scipy legitimately differ between them.

uv was chosen over the alternatives for these reasons:

- **pip-tools:** it would need one lockfile per Python version and platform, kept in sync by hand.
- **Poetry/PDM:** either would mean switching build tools. uv works with the existing setuptools `pyproject.toml`.
- **Conda:** it is unnecessary, because every native dependency (QuantLib, NautilusTrader, the solvers, BLAS) ships as a wheel.

`requirements.lock` is a hashed export of the same lock for plain pip. It is generated, never edited by hand.

## Using it

```bash
# exact environment (recommended)
uv sync --locked
uv run python -m unittest discover -s tests -v

# plain pip, exact and hash-verified
python -m pip install --require-hashes -r requirements.lock
python -m pip install --no-deps -e .
```

`uv sync --locked` refuses to run if `uv.lock` is stale relative to `pyproject.toml`.

## Changing dependencies

1. Edit the range in `pyproject.toml`, or run `uv lock --upgrade-package <name>` to move one pin.
2. Run `uv lock`.
3. Run `uv export --frozen --no-emit-project --no-dev -o requirements.lock`.
4. If a new distribution appeared, record its license in `quantos.license_policy` and run `python -m quantos.license_policy > THIRD_PARTY_NOTICES.md`.
5. Run the full suite on 3.11, 3.12 and 3.13, then commit all changed files together.

## CI enforcement

- The **lock** job runs `uv lock --check` and verifies that `requirements.lock` is exactly the export of `uv.lock`.
- The **test** matrix installs with `uv sync --locked`, records the environment manifest, and runs the tests and demos.
- **Radar Live Smoke** installs through the hashed `requirements.lock` with pip, which keeps the pip path exercised.
- **Dependency Drift** runs weekly and on demand. It ignores the lockfiles and installs the newest versions the ranges allow, so upstream breakage shows up before anyone re-locks. It never gates merges.

The uv version used in CI is pinned (`0.8.17`) in the workflows.

## Environment manifest

`quantos env-manifest` prints a content-addressed `EnvironmentManifest`. Add `--db PATH` to persist it idempotently. The identity covers:

- interpreter implementation and version, OS, architecture and libc;
- exact versions of the full runtime dependency closure;
- native builds: the QuantLib library version, numpy's BLAS/LAPACK provider and version, and the NautilusTrader version (or why it is absent);
- installed CVXPY solvers;
- SHA-256 of `uv.lock` and `requirements.lock`;
- `lock_consistency`: `LOCKED_MATCH`, `LOCK_DRIFT` with per-package details, or `LOCK_ABSENT`;
- git revision and whether tracked files are `CLEAN` or `DIRTY`.

Capture time, hostnames and paths are excluded, so identical environments share one manifest id.

Research results intended for audit should cite the `manifest_id`, for example as an evidence reference on a Research Run Manifest or simulation run. A result produced under `LOCK_DRIFT`, `DIRTY` or `UNKNOWN` is not reproducible and should be treated that way.
