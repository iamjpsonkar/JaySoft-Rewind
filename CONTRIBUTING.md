# Contributing to Rewind

Maintainer: Jay Prakash Sonkar ([iamjpsonkar](https://github.com/iamjpsonkar), [iamjpsonkar@gmail.com](mailto:iamjpsonkar@gmail.com)).

## Setup and checks

Use Python 3.11 or 3.12 from the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
ruff check src tests examples scripts
mypy src/rewind
python -m pytest -q
python -m build
./scripts/verify_offline.sh
```

Docker verification needs a daemon and network access during image build. Use `./scripts/verify_offline.sh --skip-build` to reuse `rewind-local:alpha`. Runtime verification uses `--network none`; host tests alone do not prove OS network isolation.

## Change workflow

1. Create a focused feature/fix branch and keep changes small enough to review.
2. Commit coherent increments using `feat(replay): ...`, `fix(storage): ...`, `test(asgi): ...`, or `docs: ...`.
3. Use your own real Git author identity and preserve authorship when incorporating another contributor's commits. Never fabricate contributors or backdate history.
4. Add meaningful regression/conformance tests using synthetic data, run relevant checks, and push the branch.
5. Open a PR describing the problem, resulting behavior, validation, and limits. Merge after required checks pass and review findings are resolved; preserve traceable PR history.

Parallel contributors should use separate worktrees, agree on file/module ownership, and communicate interface changes. Integrate one branch at a time and rerun affected checks. Keep optional integrations out of mandatory core dependencies.

## Review contract

Capture preserves application-visible outcomes and cancellation. Replay rejects missing, extra, reordered, transformed, or unsupported interactions without live fallback. Incomplete captures must never report successful reproduction.

Snapshot schema, matching, policy, fingerprints, and adapter support changes need compatibility notes and focused tests. Errors must be bounded and avoid recorded values. Do not introduce pickle, arbitrary deserialization hooks, or artifact-selected imports.

Reproduction tests assert recorded outcomes; regression tests assert intended fixed behavior. Do not invent business assertions from a failure recording. Benchmarks and support claims require observed evidence.

Use GitHub issues for ordinary bugs with runtime, version, and a synthetic reproduction. Follow [SECURITY.md](SECURITY.md) for vulnerabilities or sensitive data.
