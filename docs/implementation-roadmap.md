# Remaining implementation plan

Baseline: `0.1.0a2`, published on PyPI on 2026-10-05. The original project plan
contains both deliverables and future ideas. This checklist tracks executable
capabilities against the original M1–M16 milestones and the refined repository
plan. A capability is complete only with tests, documentation, and integrated CI.

## Existing evidence

- [x] M1: packaging, repository, CI, initial PyPI publication.
- [x] M3–M5: bounded versioned snapshots, in-memory recording, typed retention.
- [x] M7–M10: sequential async HTTPX capture, strict replay, FastAPI/ASGI.
- [x] M13 baseline: explicit time/date/UUID/random/sleep observations.
- [x] M14 baseline: conservative privacy defaults and secret-key filtering.
- [x] M15–M16 baseline: list/inspect/replay/delete and reproduction tests.
- [x] Bounded background persistence, lifecycle controls, measured benchmarks.

## Feature batches in progress

| Batch | Scope | Acceptance evidence |
| --- | --- | --- |
| Database | SQLAlchemy 2.x + SQLite DBAPI boundary; execute/fetch/metadata/generated IDs/transactions | Real SQLite and SQLAlchemy capture; replay with connection creation forbidden; query, argument, fetch-order divergence; bounded privacy failures |
| Redis | redis-py sync/async commands, byte/string results, errors, pipelines | Command/pipeline conformance; fake and disposable real-server evidence; no replay network fallback |
| Synchronous frameworks | Sync callable, sync HTTPX, WSGI/Flask | Return/exception/thread isolation; real Flask request and close lifecycle; fresh-process replay |
| Portable tooling | Export/import, doctor, timeline, safe retention expressions, environment observations, finite Decimal codec | Malformed/oversized/untrusted artifacts rejected; no import-time application execution; env secrets excluded; explicit predicate grammar |
| Execution diagnostics | Bounded optional function spans and tracing modes | Sync/async nesting, recursion, failures, dropped diagnostic counts without losing required interactions |
| Comparison and tests | Explicit changed-source comparison, developer-supplied desired outcome assertions | Strict mode unchanged; comparison labeled; changed required interactions still fail; no guessed business oracle |
| Integration | Combined HTTP + DB + Redis + sources fixture, docs, dependency extras, benchmarks, distribution checks | Full checks and offline end-to-end replay on supported Python versions |

## Completion ledger

- [ ] M2/M6: optional function timeline and defined tracing modes.
- [ ] M5 extension: bounded condition grammar without `eval`.
- [ ] M7/M10 extension: synchronous HTTP and Flask/WSGI.
- [ ] M11: documented SQLAlchemy/SQLite combination.
- [ ] M12: documented Redis command/pipeline subset.
- [ ] M13 extension: environment access and sync waits.
- [ ] M14 extension: configurable redaction keys, adapter privacy evidence.
- [ ] M15 extension: export/import, doctor, optional timeline inspection.
- [ ] M16 extension: labeled comparison and explicit desired-outcome tests.
- [ ] Updated compatibility matrix, README, examples and integration checks.

## Release and scope gates

The next development candidate is `0.1.0a3`. Existing published artifacts and
`v0.1.0a2` remain immutable. Feature branches use real commits and timestamps,
regular pushes, and reviewed integration PRs.

Kafka, Celery, cloud SDKs, full filesystem replay, distributed scheduling, and
arbitrary process checkpointing were future proposals, not specified adapters.
They require separate contracts and cannot be represented as implemented by a
generic mock. Transparent global time/random patching is not equivalent to
explicit source replay and needs separate conformance evidence.

Production readiness remains an evidence gate: representative workload budgets,
data-policy review, stronger isolation and deployment-specific validation.
Neither completing the feature checklist nor a synthetic benchmark certifies
production safety or a universal overhead percentage.
