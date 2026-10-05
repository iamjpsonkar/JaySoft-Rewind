# Local storage, encryption, and retention

`LocalStore` publishes validated snapshots atomically into a private directory.
`EncryptedLocalStore` provides the same save/load/list/delete/prune interface while
writing authenticated ciphertext instead of snapshot JSON. Both work with
`BackgroundWriter` and accept the same retention and quota settings.

## Coordinated local persistence

On POSIX, every store operation acquires an advisory lock on the store directory.
Cooperating Rewind processes opening the same directory serialize publication,
eviction, deletion, pruning, loading, and listing. Each operation opens its own
directory descriptor, and a store reused after a fork replaces its inherited
threading lock. An advisory lock is not a hard I/O timeout; a stuck filesystem
operation may continue beyond a background writer's shutdown deadline.

This coordination is intended for a local POSIX filesystem with working `flock`
and directory `fsync`. Processes must use the same artifact format, retention,
and quota settings for that directory. It does not coordinate with programs that
ignore the lock, protect against hostile processes running as the same user, or
establish distributed-lock guarantees on network filesystems. Use separate
directories for plaintext and encrypted stores. On non-POSIX platforms, the
existing per-instance thread lock remains; cross-process coordination and
directory-sync guarantees are unavailable.

Save performs these operations while holding the lock:

1. Validate the snapshot and compute quota/retention candidates.
2. Write a private temporary file, flush it, and synchronize its contents.
3. Publish with atomic replacement, or an atomic no-clobber hard link when
   `save(snapshot, overwrite=False)` is requested.
4. Synchronize the directory entry before deleting previous evidence.
5. Evict selected old artifacts, remove any temporary link, and synchronize
   directory changes again.

Publication can temporarily exceed the retained-artifact quota. Directory sync
improves metadata persistence but does not establish a universal hardware or
power-loss guarantee. An error after publication can leave the new artifact
present even though save raised. If the first directory sync fails, existing
artifacts selected for eviction are preserved. Errors during later eviction can
leave a partially completed retention operation; call `prune()` after resolving
the storage failure. File and directory sync errors are reported, not silently
treated as success.

The byte quota counts retained artifacts of the store's own format. It excludes
filesystem overhead and transient publication files. A process crash may leave
`.pending-*` files; they are reserved temporary names and are not automatically
counted as readable snapshots or deleted by retention. Operators can remove
confirmed orphan temporary files while all store users are stopped. Atomic file
publication alone is not a WAL or an all-files transaction.

## Explicit retention without new captures

```python
from rewind import LocalStore

store = LocalStore(
    ".rewind/snapshots", max_bytes=256 * 1024 * 1024, retention_seconds=86400
)
result = store.prune()
print(result)
# {"removed": ..., "removed_bytes": ..., "remaining_items": ..., "remaining_bytes": ...}
```

`prune()` removes expired artifacts and then enough oldest artifacts to satisfy
the configured quota. It is synchronized with other operations and flushes the
directory after deletion. Call it from an application's existing scheduler or
maintenance command. Rewind does not start a global retention daemon. TTL uses
filesystem modification times; it is an operational retention policy, not an
authenticated creation-time rule or secure erasure promise.

`ids()` lists regular artifacts with valid snapshot filenames. Load rejects
symlinks, FIFOs, non-regular files, oversized artifacts, and mismatched internal
snapshot IDs. Save/prune reject non-regular entries in their artifact namespace
before selecting evictions.

## Authenticated encryption

Install the optional dependency:

```sh
pip install 'jaysoft-rewind[encryption]'
```

The application supplies its key explicitly. An example using an existing secret
configuration is:

```python
import os
from rewind import EncryptedLocalStore

key = bytes.fromhex(os.environ["REWIND_STORAGE_KEY_HEX"])
store = EncryptedLocalStore(
    ".rewind/encrypted", key=key,
    max_bytes=256 * 1024 * 1024, retention_seconds=86400,
)

# With a captured Snapshot:
# store.save(snapshot)
# recovered = store.load(snapshot.id)
# report = rewind.replay_sync(recovered, application_entrypoint)
```

Generate and retain a random key using your application's secret-management
process. AES-GCM accepts immutable 16-, 24-, or 32-byte keys; a random 32-byte key
is a suitable default. The store neither generates a replacement key on startup
nor saves keys alongside artifacts. Losing the key makes existing artifacts
unrecoverable. Core `import rewind` remains usable without the optional crypto
package; the dependency is imported when constructing an encrypted store.

Each save obtains a fresh 12-byte nonce from the operating system. The versioned
binary envelope contains a fixed header, nonce, and AES-GCM ciphertext with its
16-byte authentication tag. Associated data binds the envelope version and the
snapshot ID used in the filename. Renaming an encrypted artifact to another ID,
tampering with the ciphertext, or supplying the wrong key fails authentication.
Ciphertext is size-bounded before decryption, and the decrypted snapshot must
pass normal schema/codec limits and ID checks before it is returned.

Both final `.rewind.enc` artifacts and temporary publication files contain only
the encrypted envelope. The store never writes a plaintext staging artifact.
Snapshot IDs, file sizes, modification times, file counts, and the envelope version
remain visible. Encryption protects stored artifact content; it does not protect
plaintext in application memory, Python objects, process dumps, or caller-created
exports. Redaction and eligibility policies still apply before encryption.

Encrypted quota accounting includes the header, nonce, and authentication tag.
Listing, deletion, and pruning use filesystem metadata and do not authenticate
each artifact's contents. Authentication happens on load. A private directory
and normal filesystem permissions remain necessary.

Key rotation is an explicit application operation: load using the old store/key
and save into a separate store using the new key, verify, then apply the chosen
retention policy. Rewind does not implement key discovery, a key ring, KMS access,
automatic rotation, or remote storage identity management.

The ordinary CLI expects plaintext portable snapshots and does not accept an
encrypted artifact or discover its key. Load through `EncryptedLocalStore` and
use the in-process replay APIs. Writing the returned `Snapshot.raw` into a file
or portable export is an explicit plaintext export by the caller.

Implementation references: [Python advisory locks](https://docs.python.org/3/library/fcntl.html),
[directory synchronization semantics](https://man7.org/linux/man-pages/man2/fsync.2.html),
and [AES-GCM authenticated encryption](https://cryptography.io/en/latest/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM).
