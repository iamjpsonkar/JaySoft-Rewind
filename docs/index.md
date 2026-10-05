# Rewind documentation

[Project home](../README.md) · [PyPI package](https://pypi.org/project/jaysoft-rewind/) · [Changelog](../CHANGELOG.md)

Start with a synthetic failure, then connect the dependencies your application uses. These guides describe the development source (`0.1.0a3`) and label features that already exist in published `0.1.0a2`.

## Start here

| Your goal | Read this |
| --- | --- |
| Get the right version and extras | [Installation](installation.md) |
| Capture a failure and generate your first test | [Step-by-step quick start](getting-started.md) |
| Look up a command or replay result | [CLI reference](cli.md) |
| Understand an unexpected result | [Troubleshooting](troubleshooting.md) |
| See the package overview prepared for PyPI | [PyPI guide](pypi.md) |

## Connect an application

| Integration | Guide |
| --- | --- |
| Async HTTPX and FastAPI/ASGI | [HTTP and FastAPI](http-and-fastapi.md) |
| Sync callable, HTTPX and Flask/WSGI | [Synchronous applications](synchronous.md) |
| SQLAlchemy and SQLite | [Database observations](database.md) |
| redis-py commands and pipelines | [Redis observations](redis.md) |
| Time, randomness, UUIDs and environment | [Explicit sources](sources.md) |

## Work with recordings

- [Function timelines and spans](tracing.md): bounded optional execution diagnostics.
- [Portable tooling and capture policy](portable-tooling.md): archives, conditions and configurable redaction.
- [Compare changed code](comparison.md): supply the expected outcome for a regression test.
- [Background persistence](background-persistence.md): queue budgets, drain and shutdown behavior.
- [Operations](operations.md): enable/disable, metrics, storage and rollback.

## Understand the contracts

| Reference | What it answers |
| --- | --- |
| [Support matrix](support-matrix.md) | Which Python versions, libraries and operations are covered? |
| [Snapshot format](snapshot-format.md) | What does a recording contain and how is compatibility checked? |
| [Security](../SECURITY.md) | What does the replay guard protect, and how should recordings be handled? |
| [Performance methodology](performance.md) and [sample results](benchmarks/README.md) | What was measured, and what do the numbers mean? |
| [Implementation ledger](implementation-roadmap.md) | Which milestones are implemented and which evidence gates remain? |

## Maintain the project

[Contributing](../CONTRIBUTING.md) covers setup, checks and PRs. [Releases](releases.md) covers building and publishing `jaysoft-rewind` through TestPyPI and PyPI.

`README.md` is the GitHub entry point, with navigation and expandable examples. `docs/pypi.md` is the package long description selected by `pyproject.toml`; its links are absolute so they work on PyPI. Update version labels in both when publishing. GitHub supports [collapsed sections](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/organizing-information-with-collapsed-sections); the package overview follows [PyPA's README guidance](https://packaging.python.org/en/latest/guides/making-a-pypi-friendly-readme/).
