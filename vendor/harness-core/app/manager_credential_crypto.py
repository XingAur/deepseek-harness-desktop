from __future__ import annotations

import base64
import binascii
import os
import re

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _CRYPTOGRAPHY_AVAILABLE = True
except ImportError:  # readonly UI/analysis must still boot without crypto extras
    _CRYPTOGRAPHY_AVAILABLE = False

    class InvalidTag(Exception):
        pass

    AESGCM = None  # type: ignore[assignment,misc]


MASTER_KEY_ENVIRONMENT_VARIABLE = "HARNESS_MANAGER_CREDENTIAL_MASTER_KEY"
CIPHER_VERSION = "aesgcm.v1"
NONCE_BYTES = 12


class CredentialEncryptionUnavailable(RuntimeError):
    """Raised when credential encryption cannot be safely initialized."""


class CredentialDecryptError(RuntimeError):
    """Raised when encrypted credential data cannot be authenticated."""


def credential_aad(
    *,
    scope_type: str,
    scope_key: str,
    provider: str,
    profile_key: str,
    field: str,
) -> bytes:
    parts = (
        "harness.manager.credential.aad.v1",
        scope_type,
        scope_key,
        provider,
        profile_key,
        field,
    )
    encoded_parts = (part.encode("utf-8") for part in parts)
    return b"".join(len(part).to_bytes(4, "big") + part for part in encoded_parts)


class AesGcmCredentialCipher:
    def __init__(self, master_key: bytes) -> None:
        if not _CRYPTOGRAPHY_AVAILABLE:
            raise CredentialEncryptionUnavailable("encryption_dependency_missing: cryptography")
        if not isinstance(master_key, bytes) or len(master_key) != 32:
            raise CredentialEncryptionUnavailable("encryption_unavailable")
        self._cipher = AESGCM(master_key)

    @classmethod
    def from_environment(cls) -> "AesGcmCredentialCipher":
        encoded_key = os.environ.get(MASTER_KEY_ENVIRONMENT_VARIABLE, "")
        try:
            if not encoded_key or encoded_key != encoded_key.strip():
                raise ValueError("missing or padded environment value")
            master_key = _decode_urlsafe_base64(encoded_key)
        except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
            raise CredentialEncryptionUnavailable("encryption_unavailable") from exc
        if len(master_key) != 32:
            raise CredentialEncryptionUnavailable("encryption_unavailable")
        return cls(master_key)

    def encrypt(self, plaintext: str, *, aad: bytes) -> str:
        if not isinstance(plaintext, str):
            raise TypeError("plaintext must be a string")
        nonce = os.urandom(NONCE_BYTES)
        ciphertext = self._cipher.encrypt(nonce, plaintext.encode("utf-8"), aad)
        payload = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
        return f"{CIPHER_VERSION}.{payload}"

    def decrypt(self, encrypted: str, *, aad: bytes) -> str:
        try:
            prefix = f"{CIPHER_VERSION}."
            if not isinstance(encrypted, str) or not encrypted.startswith(prefix):
                raise ValueError("unsupported ciphertext")
            encoded_payload = encrypted[len(prefix) :]
            payload = _decode_urlsafe_base64(encoded_payload)
            if len(payload) < NONCE_BYTES + 16:
                raise ValueError("ciphertext is too short")
            plaintext = self._cipher.decrypt(payload[:NONCE_BYTES], payload[NONCE_BYTES:], aad)
            return plaintext.decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError, ValueError, binascii.Error, InvalidTag) as exc:
            raise CredentialDecryptError("credential_decrypt_failed") from exc


def _decode_urlsafe_base64(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        raise ValueError("invalid URL-safe Base64")
    return base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
