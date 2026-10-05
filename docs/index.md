# Rewind documentation

**Start here: [Decorate a function or class](decorator-guide.md)** — save calls when your condition matches, then inspect and replay them.

[Project home](../README.md) · [PyPI package](https://pypi.org/project/jaysoft-rewind/) · [Changelog](../CHANGELOG.md)

Start with a synthetic failure, then connect the dependencies your application uses. These guides describe `0.2.0a2` and distinguish supported boundaries from earlier alpha releases.

## Start here

| Your goal | Read this |
| --- | --- |
| Add a decorator and a condition to a function or class | [Decorator walkthrough](decorator-guide.md) |
| Plug into a server, capture a request and replay its handler | [Server integration](server-guide.md) |
| Explore a snapshot in your browser | [Snapshot explorer](explorer.md) |
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

- [Filesystem and S3 observations](filesystem-and-s3.md)
- [Repository checks and protected main](repository-governance.md)

- [External PostgreSQL and MySQL](external-databases.md)
- [Kafka and Celery](messaging.md)
- [Deployment validation](deployment-validation.md)
- [Measured synthetic staging](validation/README.md)
- [Compatibility and release policy](compatibility-policy.md)

- [Private storage, encryption and retention](storage.md)
