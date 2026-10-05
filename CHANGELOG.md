# Changelog

## 0.2.0a2 — 2026-10-05

- Add importable `@capture` for synchronous/asynchronous functions, individual
  methods, and a class's declared public methods. Conditions receive arguments,
  result, error and elapsed duration; no condition retains every admitted call.
- Capture named inputs before execution, preserving sensitive-parameter redaction
  even for positional calls. Capture plain instance state and restore a separate
  replay instance without running its constructor.
- Replay decorated functions and methods directly through `--app module:function`
  or `--app module:Class.method`, without a developer-written replay factory.
- Keep application results and exceptions unchanged when predicates or storage
  fail. Expose condition/capture counters and document unsupported class state.
- Make the decorator walkthrough the primary README and package introduction.

## 0.2.0a1 — 2026-10-05

- Add synchronous PostgreSQL/psycopg and MySQL/PyMySQL DBAPI and SQLAlchemy adapters,
  with real disposable-service tests and fresh-process no-connection replay.
- Add explicit Kafka/Celery observations, rooted filesystem operations and S3
  calls with owned lazy body handles; document supported methods and test environments.
- Add optional AES-GCM storage, local POSIX process coordination, directory sync
  and explicit retention pruning. Keep core imports free of mandatory dependencies.
- Add fixed-arrival synthetic staging budgets, independent resource measurements,
  queue/drain/rollback evidence and real-service CI/release gates.
- Add server-side capture-all/conditional request recording and same-handler offline
  replay tutorial, optional handler timeline, and a standalone interactive HTML explorer.
- Add a tested beginner guide, generic existing-backend response example and
  explicit API/schema compatibility policy. Protect main with PR and CI requirements.
- Fix expired replay database cleanup contaminating later captures; preserve native
  driver execute/ping defaults and initialize ctypes before the replay audit guard.
- Retry TestPyPI index propagation before checking and promoting identical artifacts.


## 0.1.0a3 — 2026-10-05

### Added

- Synchronous callable capture/replay, per-decorator safe retention conditions, sync HTTPX transport, and Flask/WSGI request lifecycle support.
- SQLAlchemy 2.x + SQLite DBAPI capture/replay covering execution, partial fetches, cursor metadata, generated IDs, supported errors and transaction outcomes.
- Sync/async redis-py command and pipeline capture/replay, byte/string result types, and real Redis conformance checks.
- Bounded optional function/span timelines, interaction durations, and `inspect --timeline` without capturing arguments, locals or exception messages.
- Environment observations, synchronous wait replay, additional explicit random methods, finite Decimal and typed mapping/set codecs.
- Portable checksummed export/import archives, atomic no-clobber imports, runtime `doctor`, and snapshot-ID resolution for CLI tools.
- Explicit changed-code comparison and developer-approved regression outcomes, separately labeled from strict reproduction.
- Configurable redaction keys across supported structured surfaces, SQLite result-column filtering, and conservative sensitive-query exclusion.
- Combined HTTP/database/Redis/source example and optional `flask`, `sqlalchemy`, `redis`, and `all` extras.
- Navigable GitHub README with expandable examples and FAQs; documentation hub, installation walkthrough, CLI reference and troubleshooting guides; dedicated Markdown package description and documentation links for PyPI.

### Limits

- Database support is synchronous SQLite through the documented SQLAlchemy/DBAPI boundary, not general MySQL/PostgreSQL or database state reconstruction.
- Redis scripts, pub/sub, blocking commands, WATCH/cluster behavior and uninstrumented dependencies are outside the supported boundary.
- Tracing is opt-in and bounded; generators and global line tracing are unsupported. No universal overhead or production readiness claim is made.
- Existing `0.1.0a2` artifacts and tag remain unchanged; each release uses its own validation and publication workflow.

## 0.1.0a2 — 2026-10-05

First public alpha, published as `jaysoft-rewind` on PyPI after TestPyPI verification.

### Added

- Bounded background snapshot persistence with item/byte budgets covering queued and in-flight work, isolated worker context, storage-error recovery, and fork rejection.
- Fixed-key worker statistics and high-water gauges, finite flush/shutdown, explicit dropped-item and still-running-write reports.
- Optional `Rewind(writer=...)`, thread-safe admission controls, safe metrics snapshots, synchronous/asynchronous flush and close, and eventual-persistence accounting.
- Operational and persistence guides plus a runnable background capture/replay example.
- Repeatable CPU/simulated-I/O benchmarks, separate allocation measurements, end-to-end drain/CPU accounting, and controlled item/byte saturation invariants.
- Distribution-content and isolated wheel-import checks, benchmark smoke CI, and background capture in network-disabled Docker verification.
- PyPI/TestPyPI release plan, matching version/tag validation, and a manual trusted-publishing workflow that defaults to a non-publishing rehearsal.

### Changed

- Capture finalization releases retained payload references even when persistence is rejected or fails. Recorder initialization failure releases its admission reservation and preserves application execution.
- ASGI finalization clears duplicate scope/header/body references, and late retained callbacks pass through without refilling sealed capture buffers.
- Async shutdown remains scheduled if its caller is cancelled before the default executor can dispatch it.
- `Rewind.metrics` remains a readable `Counter` view but now returns a detached copy; use `stats()` for lifecycle and memory gauges.
- Development uses complete feature branches with coordinated interfaces and README updates at feature milestones.

### Limits

- Background persistence is best effort: process exit can lose queued artifacts, and a filesystem call may outlive the shutdown deadline. Shutdown does not wait for application requests.
- Serialization remains on the capture path; queue byte accounting is not a process-RSS guarantee. Production deployment and data-policy approval remain separate gates.

## 0.1.0a1 — Unreleased

Initial local alpha for Python 3.11+, with CI targeting 3.11 and 3.12. Package publication is a separate release step.

### Added

- Bounded JSON snapshots, typed codecs, capture policies, immutable artifact bytes, and private local storage with atomic publication.
- Async callable capture, execution-local context, retention predicates, admission limits, and explicit value providers.
- Explicit clock/date, UUID, random value/collection, and async sleep sources with ordered observation replay and typed date/datetime/UUID codecs.
- HTTPX async transport recording and strict sequential replay, repeated requests, and supported transport exceptions.
- Buffered ASGI HTTP capture/replay, request-chunk handling, outcome comparison, cancellation propagation, and unsupported-state reporting.
- Fresh-process factories, timeout handling, Python audit guard, and payload-free divergence reports.
- CLI inspection, listing, deletion, replay, and pytest reproduction-test generation.
- Synthetic HTTP/FastAPI examples, development checks, and network-disabled Docker validation.

### Current limits

- Local/test environments only. Synchronous persistence; production benchmarks and background persistence are future work.
- No database, Redis, distributed-system, or full-process replay. Streaming and child-task dependency execution are unsupported.
- Python audit hooks are not an OS sandbox; stronger isolation requires an external boundary.
- Privacy filtering cannot recognize all secrets. Redacted/incomplete recordings cannot undergo strict replay.
- Source capture requires explicit calls through `rewind.sources`; standard-library aliases and third-party/native randomness remain unchanged. Sleep replay skips delay without reproducing scheduling.
- Cleanup runs on save with per-instance locking; cross-process quota coordination and directory-fsync crash durability are not provided.
