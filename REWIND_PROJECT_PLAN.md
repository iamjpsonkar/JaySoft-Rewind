# Rewind — Product, Architecture, and Delivery Plan

> Capture a failing backend request. Replay its recorded dependencies locally. Turn the reproduction into a test.

**Status:** `0.1.0a2` is published on PyPI. Development `0.1.0a3` implements database/Redis boundaries, synchronous frameworks, bounded function diagnostics, portable tooling, environment replay, configurable redaction and changed-code comparison.
**Revision:** 2026-10-05, implementation status added to the architecture and delivery plan.
**Repository baseline:** the implementation and evidence ledger is maintained in [docs/implementation-roadmap.md](docs/implementation-roadmap.md), with explicit supported boundaries in [docs/support-matrix.md](docs/support-matrix.md). Initial-alpha scope tables below preserve the original staged design; they do not override the current implementation ledger.
**Decision convention:** “must” defines the target contract. Design sections below include future requirements; their presence does not imply completion. The implementation status below and README describe the actual alpha scope. Runtime validation and CI outcomes must be reported separately from configuration/documentation completion.

## Current implementation and remaining gates

| Area | Local alpha status | Remaining evidence or limitation |
|---|---|---|
| Identity | `jaysoft-rewind` published at `0.1.0a2`; `rewind` import/CLI; Jay Prakash Sonkar as maintainer | Each subsequent candidate needs its own release validation |
| Capture | Sync/async callable, ASGI/WSGI HTTP, sync/async HTTPX; per-decorator safe conditions | Sequential owning-task/thread dependencies; no global interception or arbitrary streaming replay |
| Deterministic sources | Explicit clocks, dates, UUIDs, random operations, environment reads, sync/async waits | No stdlib/global patching, third-party/native RNG interception, RNG state restoration, or scheduling replay |
| Database | SQLAlchemy 2.x + synchronous sqlite3, execute/fetch/metadata/transaction boundaries | One declared engine/driver combination; no SQL emulator or external database state reconstruction |
| Redis | Explicit sync/async redis-py wrappers, commands, pipelines, typed results | Documented command subset; no pub/sub, Lua, WATCH, blocking or cluster support |
| Diagnostics | Bounded optional function/span ring, interaction durations, CLI timeline | Explicit selected functions; no locals/args or global line profiler; optional event loss reported |
| Replay | Ordered strict matching, explicit outcome comparator, unused/extra interaction checks | Does not reconstruct heap, thread scheduling, or distributed state |
| Runner | Fresh interpreter, finite timeout, Python audit guard before application import | Audit hooks are not an OS sandbox; Docker network-disabled validation supplied separately |
| Data | Bounded JSON, typed codecs including Decimal/maps/sets, configurable key and DB-column filtering, immutable snapshots | Filtering is not complete secret/PII detection |
| Persistence | Synchronous local store or bounded background writer, atomic publication, cleanup after successful save | No durable queue, cross-process quota coordination, periodic cleanup, or directory-fsync crash durability |
| Operations | Enable/disable, fixed-key metrics, active/queued accounting, bounded writer drain and shutdown | Shutdown excludes active application requests; an in-flight filesystem call cannot be cancelled |
| Performance tooling | CPU/simulated-I/O benchmark, separate allocation pass, gated saturation checks | Synthetic closed-loop results are not production budgets or fixed-arrival load evidence |
| Compatibility | Strict fingerprints plus separately labeled changed-code comparison | Only comparison permits a changed code digest; other identity and interaction checks remain strict |
| CLI and tests | Inspect/replay/compare/list/delete/export/import/doctor; reproduction and explicit-oracle regression tests | Desired fixed behavior must be supplied by the developer |
| Release engineering | Python 3.11/3.12 CI, lint/type/test/build jobs, Docker smoke command | Record actual CI and container results before calling a release validated |
| Production | Deferred | Benchmarks, failure storms, operational controls, stronger data policy and deployment review |

The previous design-only baseline is historical. No production-readiness, quantified performance, or full acceptance-matrix completion is claimed. CI installs dependency versions within declared ranges; the resolved versions in each run define that run's evidence.

## 1. Product decision

Rewind will reproduce supported request failures by re-executing application code against recorded dependency outcomes.

The first product must answer three questions clearly:

1. Was the request captured with enough information for the declared replay scope?
2. Did the local execution consume the expected interactions and reproduce the observed outcome?
3. If it diverged, what was the first meaningful difference?

The unit of capture is one execution of a declared entry point. The portable artifact contains its inputs, recorded interactions, outcome, and compatibility metadata. Application code and a compatible local runtime are supplied separately.

Rewind does not initially checkpoint a process, recover arbitrary heap state, restore a database, reproduce thread scheduling, or replay a distributed system. “Execution snapshot” refers to this bounded recording, not a memory snapshot.

**Product promise:** for supported entry points and declared dependency boundaries, reproduce the observed failure or report why faithful replay could not be established. Never quietly substitute live dependencies for missing recorded data.

## 2. Intended user and evidence of value

The initial user is a Python backend developer investigating a failure caused by an external HTTP response, timeout, or malformed payload. The developer has access to the application's code and can run a small local test environment.

The first use case is a request that fails while interpreting a recorded payment-provider response. Fixtures must use synthetic data. A payment-shaped example does not imply certification for handling real payment data.

The core workflow is:

```text
Run application with capture enabled
  → invoke a failing request
  → retain a bounded recording
  → transfer the artifact to a compatible local checkout
  → replay with outbound network access denied
  → reproduce the observed outcome or show the first divergence
  → generate a reproduction test
  → fix the application and supply the desired assertions
```

Before expanding integrations, validate the workflow with three representative failures: a malformed response, an HTTP error response, and a transport exception. Measure time to obtain a usable reproduction against the existing manual process. User demand and time savings are hypotheses until observed; no adoption or productivity multiplier is claimed.

## 3. Release scope

| Capability | v0.1 local alpha | Later gate |
|---|---|---|
| Entry point | One async callable, then one FastAPI/ASGI request adapter | Additional frameworks and synchronous handlers |
| Outbound dependencies | Explicitly configured HTTPX `AsyncClient` transport | Requests, synchronous HTTPX, other clients |
| Interaction execution | Sequential awaited calls in one execution | Concurrent dependency calls and task causality |
| Request/response bodies | Bounded buffered JSON or bytes with explicit capture policy | Streaming, multipart uploads, SSE, WebSockets |
| Capture trigger | Exceptions, final HTTP status, duration thresholds, or always retain | Optional expression language only after a separate safety review |
| Determinism | Explicit clock/date/UUID/random/sleep methods and custom value providers | Proven transparent interception for selected APIs |
| Persistence | Versioned local JSON artifact, atomic publication | Export archives, remote storage, encryption integration |
| Replay | Strict matching, recorded outcomes, mismatch report | Controlled comparison across changed application code |
| CLI | Inspect, replay, list, delete, reproduction-test generation | Export/import conveniences, doctor |
| Database/Redis | Explicitly unsupported | One adapter/driver combination at a time |
| Deployment | Local and controlled test environments | Separate staging and production readiness gates |

Multiple independent requests may run concurrently during capture; their recordings must remain isolated. v0.1's sequential restriction concerns dependency calls inside a single captured execution.

The local alpha is not production-ready. The minimum useful replay can be proved without implementing a generic Python tracer, a dashboard, a condition DSL, or every external service.

## 4. Six design questions and answers

| Question | Working answer |
|---|---|
| What problem is solved? | Reconstructing the dependency observations that caused a request failure. |
| Who experiences it, and how often? | Python backend developers; incident frequency must be measured with initial users. |
| What is the cost of leaving it unsolved? | Manual fixture creation, reliance on changing environments, and failures that disappear before investigation. Quantify during the pilot. |
| What exists here already? | The local alpha implementation and development checks listed in the current-status table. The original repository-only baseline has been superseded. |
| What is the smallest useful solution? | One async entry point, one HTTP transport, a portable recording, and strict offline replay in a fresh process. |
| What is hardest? | Preserving the behavior visible to application code while capturing only bounded, permitted data. The central assumption is that the supported boundary observations are sufficient for the chosen failures. |

## 5. Non-negotiable invariants

| ID | Requirement |
|---|---|
| INV-01 | Capture must not change application return values or consume application-owned data invisibly. |
| INV-02 | Recorder failures must not replace the application's result or exception; cancellation semantics must be preserved. |
| INV-03 | Recordings from different executions must never mix. |
| INV-04 | An unmatched replay operation must fail explicitly; supported adapters must never fall through to live I/O. |
| INV-05 | Truncated, dropped, unknown-required, or unfinished interactions must make strict replay ineligible. |
| INV-06 | Retained values must reflect the observation time, not later mutations of the same objects. |
| INV-07 | Persisted data must pass the capture/redaction policy before storage, and before any future persistence queue. |
| INV-08 | A reproduced result requires compatible replay conditions, all required interactions consumed, and an explicit outcome comparison. |
| INV-09 | A recording cannot determine the intended business behavior after a bug fix. |
| INV-10 | Claims of support and overhead require executable evidence for a documented compatibility matrix. |

These invariants define release gates. A passing static scan alone establishes none of them.

## 6. Capture lifecycle

```text
Admission decision
  → execution-local recorder
  → bounded immutable interaction capture
  → application finishes or raises
  → evaluate retention predicate
      false → discard
      true  → seal → enqueue → persist atomically
```

Admission and retention are different decisions. Admission happens before execution and determines whether enough resources are reserved to record. Retention happens after the outcome is known. A request rejected by admission cannot later be fully recovered because it failed.

Use a context-local recorder handle with an explicit close/seal state. Create and reset the context token in `try/finally`. Install transport wrappers during client setup, not by repeatedly patching global methods for each request. Outside an active capture context, wrappers preserve ordinary behavior.

Nested capture scopes join the existing recorder in v0.1; they do not create competing recordings. Child tasks may inherit context references, so sealed recorders must reject late writes. Known concurrent child dependency operations mark the recording unsupported for strict v0.1 replay. Arbitrary background tasks and thread handoffs remain outside the declared scope; the tool must not claim to detect every uninstrumented task.

An ASGI adapter observes request input and the actual response lifecycle. It must preserve body consumption and exceptions. Only bounded, non-streaming responses are eligible initially. A body failure after response headers, disconnect, cancellation, or an unfinished outbound call produces diagnostic information and an explicit incomplete reason, not a successful replay claim.

Decorator usage requires documented ordering relative to framework route registration. The HTTP-status trigger belongs to the framework adapter; an arbitrary Python callable has no implicit HTTP status.

## 7. Two recording channels

Rewind needs two channels with different loss semantics.

| Channel | Purpose | Overflow behavior |
|---|---|---|
| Required interactions | Inputs and dependency outcomes needed by replay | Stop adding required payloads, mark incomplete, retain a bounded diagnostic summary |
| Optional diagnostics | Selected spans, timestamps, and debugging annotations | May use a ring buffer; report dropped count |

A ring buffer cannot silently evict required interactions and still claim replay completeness. Stop retaining new payloads once the replay budget is exhausted; keep the application running.

At interception time, create a detached representation using a small explicit codec set. Final JSON encoding, hashing, and disk writes can happen later, but copying and policy application are part of capture cost. Do not retain raw mutable dictionaries, framework request objects, coroutine objects, ORM entities, or entire tracebacks.

No general-purpose object serializer, pickle, dynamic deserialization imports, or arbitrary `repr()` invocation is permitted. Unsupported values produce a typed diagnostic marker and an eligibility reason. Codecs must bound nesting depth, container length, and total bytes.

## 8. Snapshot contract

The initial artifact is one UTF-8 JSON document with a `.rewind.json` suffix. Avoid designing an archive and a database simultaneously. Binary bodies use an explicit base64 encoding with decoded-length limits.

| Field | Meaning |
|---|---|
| `schema_version` | Artifact format version; initially provisional `0.1` |
| `snapshot_id` | Unique identity independent of application nondeterminism |
| `producer` | Rewind version and adapter/codec versions |
| `application` | Entry-point label, code revision, dirty-state indicator, runtime/dependency fingerprint |
| `capture` | Admission/retention details, limits, completeness, dropped counts, unsupported boundaries |
| `policy` | Capture/redaction policy identity and version; no secret policy material |
| `input` | Normalized callable input or HTTP request representation |
| `interactions` | Ordered required operations and their outcomes |
| `outcome` | Observed return/response or sanitized exception fingerprint |
| `diagnostics` | Optional timeline and operational information |

The entry-point label in an artifact is descriptive. Replay uses a local configuration mapping or an explicit `--app` supplied by the developer. Importing an artifact must never automatically import or execute a module named by that artifact.

Each interaction needs an ID, execution scope, sequence, adapter and codec version, dependency identity, operation, matchable input, and exactly one outcome: value or exception. Duration and wall time are diagnostic fields. They do not control matching.

Illustrative shape, not a finalized schema:

```json
{
  "schema_version": "0.1",
  "snapshot_id": "example-001",
  "producer": {"rewind": "0.1.0a1", "adapters": {"httpx": "0.1"}},
  "application": {"entrypoint": "demo.checkout", "revision": "example-revision", "dirty": false},
  "capture": {"complete": true, "scope": "sequential-http", "ineligible_reasons": [], "dropped_required": 0},
  "policy": {"id": "synthetic-demo", "version": "1"},
  "input": {"kind": "callable", "arguments": {"order_id": "order-demo"}},
  "interactions": [
    {
      "id": "i1",
      "scope": "root",
      "sequence": 1,
      "adapter": "httpx",
      "codec_version": "0.1",
      "dependency": "gateway",
      "operation": "http.request",
      "input": {"method": "GET", "url": "https://gateway.example/orders/order-demo", "headers": [], "body": {"encoding": "base64", "data": ""}},
      "outcome": {"kind": "return", "status": 200, "headers": [["content-type", "application/json"]], "body": {"encoding": "base64", "data": "e30="}}
    }
  ],
  "outcome": {"kind": "exception", "type": "builtins.KeyError", "message": "payment_id"},
  "diagnostics": {"dropped_events": 0}
}
```

Before adopting the schema, specify required fields, byte/nesting limits, codecs for bytes and structured input, and normalization rules. Runtime metadata omitted from this compact illustration is still required by the compatibility contract.

Version rules must distinguish unknown optional diagnostics from unknown required interaction semantics. An older reader may ignore optional diagnostics; it must reject unknown required codecs, adapters, or incompatible schema versions. Preserve golden artifacts in tests. Never silently reinterpret old data.

“Complete” means complete within the declared capture scope. It cannot prove that arbitrary application code made no uninstrumented observations. Strict replay therefore also requires a supported application configuration and an enforced isolation boundary.

## 9. Strict replay and divergence

Run each replay in a fresh worker process to limit contamination from earlier runs. Fresh processes do not recreate unrecorded production globals; the application must have a reproducible initialization path.

Replay procedure:

1. Parse and validate the bounded artifact without executing application code.
2. Reject incomplete artifacts and unsupported required adapters/codecs.
3. Check the local application/runtime fingerprint. Default to refusing incompatible strict replay.
4. Establish the runner's isolation boundary before application import or initialization.
5. Load the developer-selected application and configure replay transports/providers.
6. Invoke the entry point with recorded input.
7. Consume required interactions in order and compare the outcome.
8. Emit a structured report with the first divergence and any unused interactions.

For the initial matcher, require the next recorded operation to match the actual adapter, dependency, operation, and normalized input. Sequence disambiguates retries and identical repeated requests. Do not search forward for a convenient match.

HTTP matching must define method, scheme/host/port/path, ordered query pairs, permitted headers, and body bytes. Preserve repeated query parameters and repeated headers. Any ignored field must be declared by policy and justified as irrelevant to the reproduction. Do not assume arbitrary JSON key ordering, URL rewriting, or header removal is harmless.

Use an ordered header representation rather than a plain dictionary. Preserve response types, status, permitted headers, body bytes, and supported client exception classes. Reconstruct exceptions only from an explicit adapter allowlist; never instantiate arbitrary classes from an artifact.

| Report result | Meaning |
|---|---|
| `reproduced` | All required interactions matched and were consumed, and the configured observed-outcome comparison passed |
| `diverged` | Inputs, interaction ordering, count, or observed outcome differed |
| `ineligible` | Capture incomplete, unsupported boundary, or policy removed required information |
| `incompatible` | Application, runtime, adapter, codec, or schema does not satisfy strict preflight |
| `replay_error` | Runner or adapter failed independently of the recorded application outcome |

For raised failures, compare a sanitized exception type/message fingerprint according to policy; stack line numbers are diagnostics. For framework-handled failures, compare the observed HTTP response. A generic 500 alone may be insufficient, so fixtures must provide a more specific outcome comparator where possible.

For later changed-code comparison, allow explicit compatibility overrides and label the report as a comparison run. It must not claim that the original execution was reproduced merely because all mocked calls returned something.

## 10. Dependency boundary and offline safety

The first HTTP integration should wrap HTTPX's transport interface. It provides a concrete boundary for recording supported request/response behavior and returning recorded outcomes. Prototype client exception behavior, retries, redirects, and closing semantics before declaring support.

Automatic redirects, client retries, cookies, compressed bodies, and custom auth hooks require explicit contract tests. v0.1 may reject unsupported configurations rather than pretend to cover them. Record retry attempts that actually reach the chosen transport boundary; do not invent internal attempts below it.

Application startup, authentication, lifespan hooks, DNS, and background initialization can perform I/O before a route executes. The replay profile must disable live startup dependencies and use local synthetic configuration. If this cannot be done, report incompatibility.

Adapter interception is not a general network sandbox. A raw socket or uninstrumented client can bypass it. The strict offline runner must enforce network denial outside the application process, with a documented implementation for each supported platform. The initial reference acceptance environment may be a container with networking disabled, no production credentials, and a writable temporary directory. Docker is not a dependency of the recording library.

Replay application code is trusted local code, not artifact content. No claim is made that ordinary Python interception safely runs malicious application code. Filesystem writes, subprocesses, native extensions, and local sockets need explicit runner isolation or exclusion; library hooks alone are insufficient.

## 11. Determinism contract

Separate diagnostic clocks from application-observed values. The recorder's own IDs and monotonic durations must not consume application replay values.

For v0.1 fixtures, provide explicit clock/ID/random providers and record their observed values in sequence. Never replace one global time value everywhere. Repeated calls can return different values, and generated IDs may feed later request bodies.

Existing direct calls to `datetime.now`, imported aliases, native RNGs, or uninstrumented environment access are outside that guarantee. Transparent interception is a later capability that must publish exactly which APIs, versions, and import patterns are covered.

Do not globally freeze the event loop's monotonic clock. Replay transport timeouts as recorded client exceptions without needing to wait the original duration. Application deadline/race behavior is a separate scheduling problem and is excluded initially.

The alpha now supplies `rewind.sources`: wall/monotonic/performance clocks (float and nanosecond variants), `datetime_now`, `date_today`, `uuid4`, `random`, `randint`, `uniform`, `choice`, `sample`, `shuffle`, and async `sleep`. Each records actual outcomes and method parameters in the strict interaction sequence. Replay does not invoke live observation factories; sleep yields once with no requested delay. It does not restore RNG state or simulate elapsed time. Typed datetime codecs preserve fold and fixed-offset timezone metadata; ZoneInfo/custom timezone classes remain unsupported. Direct stdlib calls and imported aliases remain unchanged. Privacy exclusions still make recorded values ineligible unless an appropriate fixture policy permits them.

Capture only allowlisted configuration values, never a full environment dump. Do not persist production credentials to make initialization convenient. If removing a value changes behavior that matters, require a safe local substitute or mark the artifact ineligible.

## 12. Redaction and data handling

Capture policy runs before data enters the retained recording or persistence queue. Early capture still observes sensitive process data; it does not eliminate the need for a trusted production runtime.

Default policy should exclude authorization material, cookies, credential-bearing URLs, arbitrary request/response bodies, and environment values. Body capture is opt-in by content type and field policy, with bounds. Unknown binary payloads are excluded by default. The synthetic demo uses an explicit permissive policy for known non-sensitive fixtures.

Redaction has two outputs: the permitted representation and its effect on replay eligibility. A removed header may be irrelevant to application logic but essential to a client auth hook. A replaced identifier may preserve equality but break a signature calculation. Do not call a transformed recording faithful without evidence for that use case.

Consistent substitutions may be offered for approved fields, with scope-limited mappings and no reverse mapping in the artifact. They still reveal relationships and must not be described as guaranteed anonymization. Do not store hashes of low-entropy secrets as a supposed safe replacement.

Every data-bearing surface needs policy coverage: inputs, dependency requests and responses, SQL parameters/results when added, exception messages, diagnostics, configuration, and CLI output. Never dump locals by default. Reject or sanitize terminal control characters when displaying untrusted captured strings.

Local storage should use restrictive file permissions and a private directory. Production adoption requires an approved encrypted storage location, access control, retention/deletion policy, and sharing procedure. These controls are requirements for the deployment profile, not capabilities that a JSON file magically supplies.

Artifact loading is data-only. Bound total file size, nesting, decoded body size, and interaction count. Reject malformed envelopes and duplicate/invalid interaction identities. If archives are added later, defend against path traversal, symlinks, and decompression bombs before import is enabled. Checksums detect accidental corruption; they do not authenticate an untrusted sender.

## 13. Memory, latency, and persistence

The first experiment must account for capture overhead on successful requests, even though those recordings are discarded. Copying, sanitizing, allocating, and intercepting still cost CPU and memory.

Initial configurable limits to test, not advertised performance guarantees:

| Resource | Starting experimental limit | Behavior at limit |
|---|---:|---|
| Decoded body retained per interaction | 64 KiB | Mark required payload truncated/ineligible |
| Detached recording per execution | 1 MiB | Stop required capture; retain bounded reason |
| Required interactions per execution | 1,000 | Mark incomplete; preserve application behavior |
| Active recording reservation per process | 64 MiB | Reject new recording admission |
| Background persistence owned payload bytes | 16 MiB including the in-flight save | Reject new snapshot enqueue with counter |
| Background persistence owned items | 128 including the in-flight save | Reject new snapshot enqueue with counter |
| Local persisted storage | 256 MiB | Apply configured oldest-first retention or reject new write |
| Default local retention | 24 hours | Delete expired artifacts through the store policy |

These are payload/accounting budgets, not an RSS guarantee. Python object overhead, copies, encoded expansion, and worker buffers must be measured separately. Queue admission must bound bytes as well as item count. Base64 and JSON encoding require separate encoded-size limits.

Budget model: total recorder memory includes active requests, queued snapshots, detached copies, and serializer workspace. Increasing concurrency must not create an unbounded additional buffer.

Production-style persistence uses a bounded worker queue. Seal the recording before enqueue; workers must not read live request objects. Write a temporary file, flush according to the selected durability policy, then publish with an atomic replace on the same filesystem. Readers only see finalized artifacts. Disk full, permission failures, quota exhaustion, and worker crashes become bounded diagnostics and counters.

The local alpha supports both synchronous `store=` persistence and optional `writer=` background persistence. The worker accepts only immutable sealed snapshots, bounds queued plus in-flight bytes/items, isolates capture context, reports failures without retaining exception messages, and rejects forked use. It does not move serialization off the request path or provide crash durability. Queued recordings can be lost on termination, OOM, or crash; request-end capture cannot capture failures that kill the process before finalization.

The underlying store lock is per instance; multiple writers do not share a quota lock. Cleanup follows successful publication, so peak disk usage can exceed the quota temporarily. Files are flushed before atomic replace, but directory-fsync crash durability is not implemented. Retention is checked on save, not by a scheduled cleanup service.

Writer shutdown has a finite drain deadline and reports how many snapshots remain, are dropped, or are still writing. Rewind stops new admissions but does not await application requests: stop incoming requests and await their completion before closing if their recordings must be included. Async shutdown uses an executor; its timeout bounds the writer wait after dispatch, not executor or event-loop scheduling. A blocked save can outlive the deadline. The persistence thread starts with an empty context and does not recursively record its own activity.

Operational metrics: admitted/rejected recordings, predicate-retained recordings, incomplete reasons, queue drops, persistence errors, queue bytes, snapshot sizes, policy exclusions, and serialization duration. Metric labels must avoid request IDs, customer identifiers, or other unbounded/sensitive values.

## 14. Performance evidence

Keep the original light-mode <5% and standard-mode <10% ideas as exploratory goals, not release claims. Drop a literal “0% overhead” promise until off-mode behavior is measured. Defer standard/deep modes until they have distinct semantics and benchmarks.

Compare an uninstrumented baseline, installed-but-disabled capture, enabled discarded captures, retained captures, and sustained retention storms. Include both a CPU-bound small response and an I/O-heavy representative request.

Measure latency distributions, throughput, CPU, RSS, queue growth, artifact size, serialization time, and shutdown drain time at fixed request rates and increasing concurrency. Report machine/runtime versions, fixture payload sizes, warmup, repeated runs, absolute deltas, and relative deltas. Test slow disks and exhausted limits explicitly.

The production gate needs agreed numeric budgets for a representative workload and evidence that admission/drop policies enforce bounds. A percentage measured on a slow external API alone cannot establish acceptable CPU-bound overhead.

## 15. Architecture and package boundaries

```text
Framework adapter → execution context → recorder → policy + codecs
                                      │              │
Dependency adapter ───────────────────┘              ▼
                                            sealed snapshot → store

Local replay runner → validator + compatibility checks → application
                              │                            │
                              └── matcher ← replay adapters┘
                                      │
                              outcome + divergence report
```

Keep the core small. Introduce interfaces only at demonstrated variation points: dependency adapters, snapshot store, codecs, and retention policy. Avoid a separate module for every hypothetical event type.

Proposed initial layout:

```text
pyproject.toml
src/rewind/
  __init__.py
  context.py
  recorder.py
  snapshot.py
  codecs.py
  policy.py
  replay.py
  matching.py
  storage.py
  cli.py
  adapters/
    httpx.py
    asgi.py
tests/
  unit/
  contract/
  integration/
  fixtures/
examples/http_failure/
benchmarks/
docs/decisions/
```

This is a design sketch, not a reason to create empty files. Evolve the layout after the first adapter works. Keep optional framework/client dependencies out of the core dependency set. Use a `src/` layout, typed public boundaries, pytest, lint/type checks, reproducible builds, and CI artifact checks.

The PyPI distribution name must be selected before publishing: `rewind` already identifies an unrelated package. The project brand, import package, distribution, and CLI command are separate choices; review all four for collisions. Do not present `pip install rewind` as installation for this project. The existing MIT license is the current repository choice.

Python 3.11 is the declared minimum; CI targets 3.11 and 3.12. The HTTPX adapter targets 0.28.x. Keep each run's resolved FastAPI/Starlette versions with its validation evidence; broad install ranges do not establish compatibility across every version.

## 16. Local alpha developer experience

The final public API follows the proven boundary design. Prefer typed configuration and callable predicates first; postpone a string expression language and never implement conditions using unrestricted `eval`.

Current configuration for synthetic fixtures:

```python
from rewind import CapturePolicy, LocalStore, Retention, Rewind

rewind = Rewind(
    application="example",
    code_paths=[__file__],
    store=LocalStore(".rewind/snapshots"),
    policy=CapturePolicy.synthetic(),
    retain=Retention(exceptions=True, status_at_least=500),
)
client = httpx.AsyncClient(transport=rewind.httpx_transport())
app = rewind.asgi(app)
```

The explicit client transport is an intentional setup requirement in the first release. Document where application clients are created and owned. Middleware attachment alone must not imply interception of every HTTP client, database, cache, or startup operation.

Current CLI:

```text
rewind inspect ./failure.rewind.json
rewind replay ./failure.rewind.json --app demo:replay_target
rewind list
rewind delete <snapshot-id>
rewind test ./failure.rewind.json --app demo:replay_target --output test_reproduction.py
```

`demo:replay_target` must return `ReplayTarget(rewind, entrypoint, kind="callable")` or `kind="asgi"`. Inspect is data-only. Replay runs developer-selected application code under its declared profile; the default is `python-guard`, not an OS sandbox. List/inspect show completeness and eligibility reasons.

Replay exits with 0 for reproduced, 1 for diverged, 2 for ineligible/incompatible, and 3 for tool failure. A nonzero application outcome may be a successfully reproduced failure; the CLI status does not simply mirror the application's 500 or exception.

## 17. Database and Redis expansion

Database support starts only after the HTTP proof and a separate adapter experiment. Select one SQLAlchemy version family, one driver, one database engine, and one sync/async execution model. Publish that exact combination.

Define whether interception occurs at a DBAPI cursor, dialect, or result boundary. SQL execution events alone do not safely capture every fetched value. The experiment must preserve `fetchone`, `fetchmany`, iteration, partial consumption, column metadata, row counts, generated keys, parameter types, and supported exceptions.

Record transaction begin/commit/rollback outcomes and relevant connection initialization. Preserve typed values such as decimals, bytes, dates, nulls, and ordered columns. Avoid handing an ORM arbitrary dictionaries where it expects cursor behavior.

This is replay of observed database interactions, not a SQL emulator. A changed query, changed parameter, or additional read must diverge. Replaying recorded commit success does not prove a modified application will preserve real database integrity. Integration tests against a disposable real database remain necessary.

Redis has the same boundary obligation: commands, byte/string mode, return types, errors, pipelines, and transaction behavior. TTL-sensitive behavior, Lua scripts, pub/sub, and blocking operations require their own support decisions. Do not claim cache state reconstruction from a list of command results.

Kafka, Celery, filesystem adapters, and cloud SDKs remain separate future proposals with entry-point, causality, side-effect, and lifecycle contracts.

## 18. Reproduction and regression tests

Test generation follows proven replay. A template-generated test should reference the sanitized artifact, use the same strict replay runner, verify interaction consumption, and assert the recorded outcome explicitly.

There are two different test oracles:

| Test | Source of expected behavior | Purpose |
|---|---|---|
| Reproduction test | Recorded failure and explicit comparator | Establish that the fixture recreates the bug |
| Regression test | Developer-approved intended result | Prove the fix and guard against recurrence |

A captured 500 does not imply that the fixed endpoint should return 200. Do not invent business assertions. If a fix changes the interaction sequence, report that the old recording is insufficient; the developer must construct/re-capture an appropriate fixture rather than silently allowing unmatched live calls.

Generated files must clearly identify fixture dependencies and privacy assumptions. Avoid duplicating sensitive bodies into Python test source when the fixture already holds them. Test generation is deterministic and template-based.

## 19. Acceptance matrix

This is the target acceptance matrix. Focused tests exercise capture, matching, policy, storage, HTTPX, ASGI/WSGI, SQLite, Redis, CLI and comparison behavior. Queue failure storms and bounded background persistence are implemented and tested. The matrix is not a blanket production claim: deployment-specific isolation, process-memory budgets and compatibility evidence remain separate validation gates.

| ID | Scenario | Required observation |
|---|---|---|
| A01 | HTTP response lacks an expected JSON field | Fresh-process replay consumes recorded response and reproduces the exception fingerprint |
| A02 | HTTP error response | Recorded status/headers/body reach application unchanged within policy; observed outcome matches |
| A03 | Supported transport timeout | Same supported client exception behavior without waiting the original timeout |
| A04 | Mutate a captured object after interception | Persisted representation remains the value seen at capture time |
| A05 | Two identical retry requests, different outcomes | First and second recorded outcomes are consumed in sequence |
| A06 | Change method, URL, body, or matchable header | First mismatch identifies field and interaction without live fallback |
| A07 | Skip an operation or issue one additional operation | Unused or unexpected interaction is reported as divergence |
| A08 | Simultaneous independent incoming requests | Unique recordings contain only their own inputs/interactions/outcomes |
| A09 | Concurrent child calls, late writes, or unfinished calls | Explicit unsupported/incomplete reason; no false reproduced result |
| A10 | Body, interaction, memory, queue, or disk limit exceeded | Application behavior preserved; bounded capture loss is visible |
| A11 | Secret in each supported data surface | It is absent from artifact, worker queue representation, CLI output, and recorder diagnostics |
| A12 | Redaction changes a value needed by computation | Strict replay is rejected or an explicitly tested substitution is applied |
| A13 | Unknown codec/schema, malformed JSON, oversized body | Load fails before application execution with a bounded diagnostic |
| A14 | Recorded return changed to another exception or status | Outcome divergence detected even if interactions matched |
| A15 | Direct socket or alternate client during replay | External runner denies the connection; no “offline” claim based only on adapters |
| A16 | Dependency access during application initialization | Denied or satisfied by explicit replay setup before handling the request |
| A17 | Recorder/serializer/storage exception | Original application result and cancellation behavior preserved |
| A18 | Atomic publication interrupted | No partially finalized artifact is accepted as complete |
| A19 | Code/runtime fingerprint mismatch | Strict preflight refuses; explicit comparison mode labels the override |
| A20 | Repeated provider values feed later request inputs | Recorded sequence is consumed; leftover values are detected |
| A21 | Request body consumption and response lifecycle | Adapter does not double-consume, drop, or alter application-visible bytes |
| A22 | Regression test generated from a failure | Observed oracle preserved; desired post-fix assertion requires developer input |

Use contract tests for adapters, golden fixtures for format compatibility, fresh-process integration tests for replay, and property/fuzz tests for bounded parsing and matching where they find meaningful edge cases. Avoid tests that only restate implementation details.

## 20. Milestones with exit gates

R0–R3 and R5 have implementation and validation evidence. Published `0.1.0a2` adds R4's bounded writer, operational controls, failure-storm tests and benchmark harness. Development `0.1.0a3` adds R6's SQLAlchemy/SQLite experiment and R7's Redis, synchronous HTTPX, Flask, environment and function-diagnostic boundaries, plus explicit desired-outcome regression tests. Environment-specific performance budgets, staging data-policy review, rollout/rollback exercises and R8 stable-release evidence remain open. The completion ledger tracks integrated checks; features alone do not establish production readiness.

| Milestone | Deliverable | Exit gate | Depends on |
|---|---|---|---|
| R0 — Scope and identity | This plan, naming decision, runtime candidates, synthetic scenario | MVP/exclusions documented; distribution naming checked | Existing repository |
| R1 — Replay feasibility | Minimal async callable + HTTP capture/store/load/replay experiment | A01–A07 demonstrated in a fresh process; A15 network denial established | R0 |
| R2 — Artifact and capture core | Schema draft, codecs, context lifecycle, limits, policy, local store | A04, A08–A14, A17–A20; golden artifacts validate | R1 feedback |
| R3 — Local alpha | FastAPI adapter, CLI, reproducible example, install/build CI | A16, A21; one documented command reproduces each of three fixture failures | R2 |
| R4 — Controlled deployment | Bounded background persistence, metrics, benchmark report, operational guide | Failure-storm/resource tests pass; staging data policy reviewed; rollback exercised | R3 |
| R5 — Reproduction tests | Template generation and explicit oracle workflow | A22; generated tests execute in clean CI without external services | R3 |
| R6 — Database experiment | One declared SQLAlchemy/driver/engine adapter | Fetch/transaction/write/error semantics proven against disposable DB capture | R4 + separate feasibility review |
| R7 — Additional boundaries | Redis, broader nondeterminism or frameworks selected by demand | Each addition has compatibility, safety, and conformance evidence | R6 or justified independent proposal |
| R8 — Stable release | Stable public contracts, compatibility policy, documentation, releases | Maintained adapter matrix and production readiness evidence; unresolved critical risks closed | Relevant prior gates |

Do not equate every milestone with a week. Recorder transparency, redaction fidelity, and database behavior are research risks; a failed experiment changes scope rather than extending an unsupported deadline indefinitely.

R1 can be a small disposable implementation. Freeze only the minimum schema needed to run it, then revise the schema from evidence. Do not spend a sprint building all conceptual modules before proving replay.

The initial v1.0 candidate is the proven HTTP path with dependable contracts. Database, Redis, Flask, and deep tracing need not all block it. They must not appear as completed features merely because they are on the roadmap.

## 21. First sprint

The first sprint should produce one artifact that reproduces one failure in a fresh isolated process. The schedule is a suggested sequence, not an effort estimate.

1. Resolve naming and choose the initial synthetic fixture and tested runtime candidates.
2. Create minimum packaging, test/build CI, and the example entry point.
3. Prototype the HTTP boundary and immutable recorded representation.
4. Save/load a provisional artifact and implement strict sequential replay.
5. Demonstrate reproduction with network disabled; change one input and demonstrate an actionable mismatch.
6. Review what the experiment disproved, revise schema/limits, and decide whether to proceed to R2.

If the boundary cannot preserve observed response/exception behavior, narrow the supported configurations before adding middleware or CLI polish. If reproduction needs broad monkeypatching or hidden process state, change the fixture or product scope and record the limitation.

## 22. Production rollout and rollback

Local alpha → synthetic staging → limited staging with approved data policy → opt-in production canary → measured expansion.

Before production: complete the supported-path acceptance matrix, run benchmarks and retention storms, configure quotas and encrypted storage, define access/retention rules, verify supported runtime versions, and exercise disable/rollback. Assign operational ownership in the deployment repository; do not invent an owner in this generic toolkit plan.

Use an admission switch that can stop new recordings immediately. Disabling capture must restore ordinary client behavior without requiring removal of middleware during a live request. Bounded queued data follows the declared drain/drop policy. Rewind never replays production mutations in the production environment as part of rollout validation.

Failure policy: preserve application availability, sacrifice recording completeness visibly. Diagnostic signals must be rate-limited so a persistence outage does not become a logging outage.

## 23. Risk register and decision log

| Risk | Consequence | Required proof or containment |
|---|---|---|
| Uninstrumented observations | Plausible but incorrect replay | Declared scope, explicit providers, first divergence, isolated runner |
| Redaction changes behavior | Non-reproducible artifact | Policy effects in eligibility; synthetic fixtures; no blanket fidelity claim |
| Request concurrency leaks context | Cross-request data exposure | A08/A09, lifecycle closure, no per-request global patching |
| Large payloads/failure storms | Production memory or disk pressure | Byte admission budgets, queue limits, retention tests |
| HTTP wrapper alters consumption | Capture introduces bugs | Client/framework conformance tests and bounded supported bodies |
| Initialization contacts real systems | Offline replay causes side effects | Isolation before import/startup and dedicated local app factory |
| DB boundary is too complex | Schedule and support explosion | One-driver experiment; no SQL emulation claim |
| Reproducing a generic error looks like success | False confidence | Explicit outcome comparator and full interaction consumption |
| Package/CLI collision | Wrong package installed | Naming verification before publication |
| Too much design before evidence | No usable product | R1 vertical experiment before broad integrations |

Proposed decisions to ratify through the experiments:

| ID | Decision | Revisit trigger |
|---|---|---|
| D01 | Record/replay supported dependency observations | Product requires true process checkpointing |
| D02 | HTTP-first, async, sequential execution scope | Initial user failures require additional boundaries |
| D03 | Required interactions cannot be silently evicted | A proven bounded alternative preserves completeness |
| D04 | Strict matching and no live fallback | Any proposed relaxed mode must be separately named and tested |
| D05 | Explicit providers before broad nondeterminism patching | Conformance evidence demonstrates transparent interception |
| D06 | JSON artifact with explicit safe codecs | Measured artifact sizes justify a container format |
| D07 | Security and resource limits precede production | Never waived merely to meet a release date |
| D08 | Observed and intended test oracles stay separate | No implicit conversion is allowed |

Resolved: distribution/import/CLI identity and publication, ordered matching, observed versus developer-supplied outcome comparison, bounded background lifecycle, and the SQLite/SQLAlchemy and Redis boundaries. Still open: broader version/driver evidence, OS isolation beyond the supplied Docker profile, representative production performance budgets and deployment validation.

## 24. Project status and change discipline

For “Rewind status,” report current milestone, completed evidence, current task, next gate, blockers, and decisions changed since the last update. Distinguish documentation completion from runtime verification.

For a feature idea, evaluate whether it improves reproduction rate or explains divergence for the initial user. Require a use case, capture boundary, privacy impact, compatibility story, and acceptance test before expanding scope.

For “implement Rn,” implement that milestone and its gate. Do not interpret this plan as permission to publish packages, change deployment configuration, or contact external teams.

## 25. References

- [HTTPX transports](https://www.python-httpx.org/advanced/transports/) — supported integration boundary.
- [Python context variables](https://docs.python.org/3/library/contextvars.html) — execution-local state.
- [SQLAlchemy cursor events](https://docs.sqlalchemy.org/en/20/core/events.html#sqlalchemy.events.ConnectionEvents.after_cursor_execute) — future database adapter constraints.
- [Existing PyPI rewind project](https://pypi.org/project/rewind/) — unrelated package; this project uses the `jaysoft-rewind` distribution name.
