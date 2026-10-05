# Changelog

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
