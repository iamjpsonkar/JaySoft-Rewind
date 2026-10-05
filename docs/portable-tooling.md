# Portable recordings and capture conditions

[Documentation home](index.md) · [Quick start](getting-started.md)

`rewind export failure.rewind.json -o failure.rewind` creates a bounded archive
containing a versioned manifest, checksum, and the validated recording. It never
includes application source, environment files, or executable Python objects.

`rewind import failure.rewind --store .rewind/snapshots` validates every member
without extracting paths and publishes the recording atomically. Existing IDs
and export destinations are never overwritten. Archives use stored ZIP members;
compressed/encrypted entries, unknown members, symlinks, corrupt checksums and
oversized payloads are rejected. A checksum detects corruption, not authenticity.
Apply the same privacy controls to archives as to the recordings they contain.

`rewind doctor` reports Python, Rewind, installed optional dependency versions,
and Docker executable availability. It does not connect to Docker or services,
read environment values, or import application code. Installed libraries do not
mean their clients are automatically instrumented.

## Conditions

`Condition.parse('exception or (status >= 500 and duration > 20ms)')` supports
`exception`, `always`, `never`, status/duration comparisons, `and`, `or`, `not`,
and parentheses. `and` binds more tightly than `or`. Durations use seconds by
default, or explicit `s`/`ms`; status values must be integers from 100 to 599.
An absent status does not match any status comparison. Expressions have finite
length, token and nesting limits. No Python `eval`, calls, attributes or indexing
are permitted. Typed `Retention` remains available.

## Environment and synchronous waits

Use `rewind.sources.getenv('REGION', 'local')` for an optional environment value
and `rewind.sources.environ('REGION')` for a required value. Replay supplies the
recorded value or supported missing-key exception without reading live variables.
Known secret keys such as `API_KEY` are excluded from these observations and
make strict replay ineligible. Arbitrary string values still require a suitable
capture policy; this does not classify every possible secret or PII value.

`rewind.sources.sleep_sync(seconds)` records completion of a synchronous wait
and skips the delay on replay. Async `sleep` retains one scheduling yield.
`getrandbits` and `randrange` complement the existing explicit random methods.
None of these methods patches global APIs or reconstructs thread scheduling.

## Application-specific redaction

`CapturePolicy(redacted_keys=('customer_id', 'email'))` adds field names to the
built-in sensitive-key rules; it does not enable value/body capture by itself.
Names are case-insensitive and ignore hyphens/underscores. The rules apply to
structured values, JSON bodies, headers, URL queries, explicit environment
observations, Redis mappings, and SQLite result-column names.

SQL statements containing sensitive identifiers have their statement and bindings
omitted. Fetch observations inspect column names and redact corresponding cells
without modifying the rows returned to the application. Required redaction makes
strict replay ineligible. This is conservative filtering, not a general SQL parser
or universal PII detector: sensitive literals without identifiable fields and
secrets copied into unrelated scalars still require an application data policy.
