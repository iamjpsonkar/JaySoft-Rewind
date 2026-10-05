"""Authenticated local snapshot storage with an explicitly supplied AES key."""

import os
from pathlib import Path

from .errors import InvalidSnapshot
from .limits import Limits
from .snapshot import Snapshot
from .storage import LocalStore, _read_file

_MAGIC = b"REWIND-AESGCM\x00\x01"
_NONCE_BYTES = 12
_TAG_BYTES = 16
_OVERHEAD = len(_MAGIC) + _NONCE_BYTES + _TAG_BYTES


class EncryptedLocalStore(LocalStore):
    """AES-GCM envelopes on disk; plaintext exists only in caller/process memory.

    Keys are never generated, loaded from artifacts, or written by the store.
    Snapshot IDs, file sizes, and timestamps remain visible filesystem metadata.
    """

    _suffix = ".rewind.enc"

    def __init__(
        self,
        path: str | Path,
        *,
        key: bytes,
        max_bytes: int = 256 * 1024 * 1024,
        retention_seconds: float = 86400,
        limits: Limits | None = None,
    ) -> None:
        if type(key) is not bytes or len(key) not in (16, 24, 32):
            raise ValueError("AES-GCM key must be immutable bytes of length 16, 24, or 32")
        # The core package remains importable without the optional crypto extra.
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self._cipher = AESGCM(key)
        super().__init__(
            path, max_bytes=max_bytes, retention_seconds=retention_seconds, limits=limits
        )

    def save(self, snapshot: Snapshot, *, overwrite: bool = True) -> Path:
        snapshot = Snapshot.from_bytes(snapshot.raw, self.limits)
        nonce = os.urandom(_NONCE_BYTES)
        associated = _MAGIC + snapshot.id.encode("ascii")
        ciphertext = self._cipher.encrypt(nonce, snapshot.raw, associated)
        return self._save_bytes(snapshot.id, _MAGIC + nonce + ciphertext, overwrite=overwrite)

    def load(self, snapshot_id: str) -> Snapshot:
        from cryptography.exceptions import InvalidTag

        with self._locked():
            raw = _read_file(self._path(snapshot_id), self.limits.snapshot_bytes + _OVERHEAD)
        if len(raw) < _OVERHEAD or not raw.startswith(_MAGIC):
            raise InvalidSnapshot("unsupported encrypted snapshot envelope")
        nonce_end = len(_MAGIC) + _NONCE_BYTES
        try:
            plaintext = self._cipher.decrypt(
                raw[len(_MAGIC):nonce_end], raw[nonce_end:], _MAGIC + snapshot_id.encode("ascii")
            )
        except InvalidTag:
            raise InvalidSnapshot("encrypted snapshot authentication failed") from None
        snapshot = Snapshot.from_bytes(plaintext, self.limits)
        if snapshot.id != snapshot_id:
            raise InvalidSnapshot("snapshot ID differs from its storage name")
        return snapshot
