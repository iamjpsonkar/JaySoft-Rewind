# Snapshot format 0.1

[Documentation home](index.md) · [Quick start](getting-started.md)

A snapshot is a bounded UTF-8 JSON document. `SCHEMA_VERSION` is `"0.1"`;
package versions such as `0.1.0a2` and `0.1.0a3` are separate producer metadata.
There is no pickle, arbitrary object reconstruction, embedded executable code,
or application module selected by artifact content.

`Snapshot.from_bytes` validates before returning an immutable byte-backed object.
`Snapshot.raw` preserves the original bytes; `Snapshot.data` returns a detached
dictionary. Reading an older artifact does not rewrite its identity, timestamp,
producer, application fingerprint, or recorded observations. `from_dict`
serializes a new supplied dictionary and validates it; it is not a migration API.

## Document structure

| Field | Meaning and validation |
| --- | --- |
| `schema_version` | Must equal the reader's supported schema string. |
| `snapshot_id` | Exactly 32 lowercase hexadecimal characters. |
| `created_at` | Producer-generated UTC ISO timestamp; the schema validates that it is a string. |
| `producer` | Producer metadata, currently `{ "version": "package version" }`; not a replay compatibility override. |
| `application` | Exactly `name`, `code_digest`, `python`, and `dependencies`. The first three are nonempty strings; dependencies map string names to string versions. |
| `policy` | The four boolean capture flags, optionally with bounded `redacted_keys`. |
| `capture` | Boolean `complete` and a list of at most 32 string `ineligible_reasons`; complete must equal whether that list is empty. |
| `input` | Entry-point `kind` plus a typed-codec `value`. |
| `interactions` | An ordered list of required dependency observations. |
| `outcome` | Required entry-point return or exception envelope. |
| `diagnostics` | Optional bounded diagnostic metadata, described below. |

The fingerprint producer hashes the explicitly declared source files and stores
Python major/minor plus installed supported adapter dependency versions. Loading
valid JSON does not establish compatibility with a local application. Strict
replay separately checks its fingerprint, policy, entry-point kind, sequential
operations, arguments, and outcome. A new reader being able to load an old
artifact does not waive environment or source compatibility checks.

Unknown top-level metadata is not interpreted as executable behavior. Known
required structures remain validated; unknown required schema versions,
entry-point kinds, operations, codec tags, and outcome kinds are rejected.
The worker loads and validates the artifact before importing the explicitly
user-selected application factory. Incomplete artifacts can be inspected but
are rejected for replay before application imports as well.

## Entry points and outcomes

The `input.value` field is always a typed-codec node. For complete recordings,
its decoded shape is:

| `input.kind` | Decoded `input.value` |
| --- | --- |
| `callable` | Exactly `{ "args": tuple, "kwargs": dict }`, for an async callable. |
| `callable_sync` | The same argument shape, for a synchronous callable. |
| `asgi` | Exactly `{ "scope": dict, "body": bytes, "chunks": list[int] }`. Chunk lengths are nonnegative, the list is nonempty and bounded, and its sum equals body length. |
| `wsgi` | Exactly `{ "environ": dict }`; input-stream reads are separate interactions. |

Incomplete recordings may have policy-excluded or truncated entry values; codec
validation still applies, while complete-entry shape checks are not imposed on
them. Framework adapters impose additional protocol checks during replay.

A normal outcome is `{ "kind": "return", "value": CODEC_NODE }`. An exception
outcome is `{ "kind": "exception", "type": "module.Class", "args": CODEC_NODE }`.
The exception type is data, never an instruction to import a class. Adapters
reconstruct only their explicit supported exception classes and argument shapes.
The capture policy determines whether arguments may be retained.

## Required interactions

Each item contains an integer `sequence` starting at 1 without gaps, an allowed
`operation`, a string logical `dependency`, a codec `input`, and an `outcome`.
An unfinished `outcome: null` is allowed only when the snapshot is incomplete.
Optional `duration_ns`, when present, is a nonnegative integer. It describes
capture timing and does not participate in strict replay matching.

| Operation | Boundary |
| --- | --- |
| `http.request` | Application-visible HTTPX request and response observations. |
| `value` | Explicit clock, random, UUID, sleep, or developer-provided values. |
| `db.call` | SQLite DB-API connection/cursor operations, including the SQLAlchemy SQLite adapter. |
| `redis.command` | One allowed Redis command. |
| `redis.pipeline` | One ordered Redis pipeline execution with its transaction/options contract. |
| `wsgi.read` | WSGI input-stream reads and their observed results. |

Adapter inputs/results can contain additional typed envelopes. For example,
database calls carry a data-only result/error envelope, and Redis preserves
its response collection types. The corresponding adapter validates and decodes
that boundary's shape. A globally recognized operation does not authorize
arbitrary methods or live fallback in an adapter.

## Typed values

Every value is an object with exactly `t` (tag) and `v` (payload). Python user
dictionaries themselves use pair-list encoding, so a user dictionary containing
`t` or `v` cannot masquerade as a codec node.

| Tag | Payload / decoded type |
| --- | --- |
| `scalar` | JSON null, boolean, integer, finite float, or string. Integers are limited to 4096 bits. |
| `bytes` | Valid Base64 string, decoded as bytes. |
| `uuid` | Canonical 32-character lowercase hexadecimal UUID. |
| `date` | Canonical ISO date string. |
| `datetime` | A codec dictionary with exactly `iso`, `fold` (0 or 1), and `name`; supports naive or fixed-offset timezones. |
| `decimal` | Canonical finite `Decimal` string with bounded length/digit count. |
| `list`, `tuple` | Lists of child codec nodes, preserving collection type. |
| `dict` | List of key/value codec-node pairs; keys must decode to distinct strings. |
| `mapping` | Pair list preserving other supported hashable key types, including bytes. Duplicate/equal or unhashable keys are rejected. |
| `set`, `frozenset` | Lists of supported hashable child nodes; duplicate/equal or unhashable members are rejected. Producers sort encoded members for deterministic serialization. |

Only supported built-in/explicit scalar types are encoded. Unknown tags,
nonfinite numbers, invalid Base64, unsupported timezone objects, arbitrary
objects, and malformed typed envelopes fail validation or make capture
ineligible. Decoding does not evaluate Python expressions or execute object
methods from the artifact.

The published `0.1.0a2` format already included scalar, bytes, UUID, date,
datetime, list, tuple, and string-key dictionary nodes. `0.1.0a3` adds Decimal,
mapping, set, and frozenset nodes. Readers without these capabilities reject
these required tags; they do not substitute `None` or discard observations.

## Policy compatibility and optional diagnostics

The original policy object has exactly four booleans: `capture_values`,
`capture_bodies`, `capture_binary`, and `exception_args`. Those documents remain
valid unchanged. Current documents may additionally contain `redacted_keys`, a
list of at most 64 nonempty strings of at most 128 characters each. Absent custom
keys normalize to an empty tuple in `CapturePolicy`; empty custom keys are
omitted when its policy document is emitted.

`diagnostics` is optional. A recognized version-1 object contains exactly
`version`, `events`, and a nonnegative integer `dropped`. At most 10,000 events
are allowed by the format; capture usually imposes a much smaller limit. Each
event has `sequence`, `span_id`, `parent_id`, `kind`, `phase`, `name`, `offset_ns`,
`elapsed_ns`, and `exception_type`. Sequence values increase, but may have gaps
because older optional events were dropped. IDs, timing values, enums, and
bounded static labels are validated. Diagnostic version values other than 1
are retained as optional data without being interpreted as version-1 events.

Optional diagnostics never replace required interactions and never establish
replay completeness. Their timestamps and timing values do not affect replay.
The recorder fits them into space remaining after required data and removes them
if optional validation would otherwise prevent a valid required snapshot from
being saved. See [function diagnostics](tracing.md) for retention behavior.

## Limits and compatibility evidence

Default limits are 1 MiB per snapshot, 64 KiB per HTTP/framework body, 1,000
required interactions, codec depth 32, and 10,000 codec items. JSON parsing also
bounds aggregate structure, rejects duplicate JSON keys, and rejects nonfinite
JSON numbers. Readers may use stricter configured limits; the artifact cannot
increase them. Validation errors raise `InvalidSnapshot` before application
factory selection or execution.

`tests/fixtures/golden-v0.1.json` is an actual 2,483-byte capture produced by the
published `jaysoft-rewind==0.1.0a2` distribution in an isolated Python 3.12
interpreter (`python -I`), using `tests/fixtures/golden_app.py`. Its source,
typed input/value observation, producer metadata, ID, timestamp, and environment
fingerprint are frozen, with no substituted version or reconstructed payload.
Its SHA-256 is
`77cd7045f07424d1d1025a773f34679ee32799c2a7f9d7a1c621ecb547c42e70`.

`tests/test_snapshot_compatibility.py` checks that current readers preserve
those bytes and typed values, match the original declared source digest, retain
four-field policy compatibility, and reject unknown required semantics before
any application factory import. The `.json` fixture extension deliberately
distinguishes this synthetic compatibility evidence from local `.rewind.json`
recordings, which are excluded from release distributions.
