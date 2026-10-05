# Supported boundaries and compatibility evidence

[Documentation home](index.md) · [Quick start](getting-started.md)

This matrix describes `0.2.0a1`. The earlier `0.1.0a2` alpha retains its
HTTPX/ASGI scope. Every adapter is explicitly configured; package installation
alone does not intercept a client or framework.

| Boundary | Supported contract | Evidence and exclusions |
| --- | --- | --- |
| Python | 3.11 and 3.12 | CI tests both; no inferred support claim for later Python releases |
| HTTPX | 0.28.x sync/async transports | Buffered request/response and allowed transport errors; streamed bodies become ineligible |
| FastAPI/ASGI | HTTP request/response through explicit middleware | Real app tests; no lifespan/WebSocket/SSE replay |
| Flask/WSGI | Flask 3.x; WSGI input, start_response, iteration, write and close | Real Flask tests; server must close response iterable; unsupported extensions become ineligible |
| Database | SQLAlchemy 2.x, Python sqlite3, synchronous SQLite | Real DB and ORM tests; partial fetches, generated keys, transactions, no-live-connection replay; no async engine claim; external drivers below |
| PostgreSQL/MySQL | psycopg 3 / PyMySQL 1.x synchronous DBAPI + SQLAlchemy 2.x | Disposable PostgreSQL 16 and MySQL 8.0.31 conformance; no SQLAlchemy 1.3 or cross-thread replay claim |
| Kafka | kafka-python 2.x producer send/future get and consumer poll | Apache Kafka 3.9.1 conformance; explicit methods, no distributed worker scheduling |
| Celery | Explicit send_task/task apply_async and documented result observations | Actual Celery eager execution and memory-broker worker tests; no arbitrary worker replay |
| S3 | boto3 put/get/head/delete_object and list_objects_v2, lazy body read/close | Real SDK with Moto emulation; no real AWS validation or generic cloud SDK claim |
| Filesystem | Rooted explicit byte/text reads/writes, exists/listdir/unlink | No disk access in replay; no global open patching or hostile-process containment |
| Redis | redis-py 5.x/6.x sync and asyncio, documented commands/pipelines | Real Redis via isolated Unix sockets, bytes and decoded-string clients; no Lua/pubsub/WATCH/blocking/cluster claim |
| Sources | Explicit clock/date/UUID/random/environment/wait methods | Ordered observations; no stdlib aliases, RNG internals or scheduler reconstruction |
| Function diagnostics | Explicit sync/async decorators and named spans | Nested/recursive calls, bounded overflow, copied contexts; no generators/global profiler/line tracing |
| Portability | Schema 0.1 data and stored ZIP archive v1 | Data-only validation, bounded members, hashes, no extraction or automatic code imports |
| Comparison | Explicit source-digest override and optional developer oracle | Runtime/dependencies/app identity/policy/kind and all required observations still checked |

Local adapter conformance has run with Python 3.12.13, HTTPX 0.28.1,
SQLAlchemy 2.1.3, redis-py 6.4.0 and Flask 3.1.3. CI additionally resolves
SQLAlchemy 2.0.x, redis-py 5.x and Flask 3.0.x on Python 3.11; the Python 3.12
job uses the current declared ranges. Each job retains `dependency-versions`
artifacts. Installation ranges are constraints, not evidence for every possible
transitive version combination.

Strict fingerprints include declared source bytes, Python major/minor, and
installed supported adapter package versions. Older artifacts remain data-loadable
when their schema/codecs are supported; runtime reproduction still requires the
recorded compatible environment. New required codecs and operations must never
be silently interpreted by old readers. Unknown optional diagnostics can be ignored.

The default audit guard blocks supported Python network/subprocess/native-loading
and direct SQLite connection events before application import. It is not an OS
sandbox. Docker checks enforce a separate network-disabled boundary. Production
deployment, arbitrary native code, distributed causality and complete filesystem
replay require separate validation and are not implied by this support matrix.

See [external databases](external-databases.md), [messaging](messaging.md),
[filesystem/S3](filesystem-and-s3.md), and the [compatibility policy](compatibility-policy.md).
